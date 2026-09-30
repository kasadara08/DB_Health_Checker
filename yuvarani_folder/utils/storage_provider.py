import os
import platform
import shutil
import ctypes
import string

# ── Module-level deploy cache: track which connections have already had the
#    Java bridge deployed so we never CREATE OR REPLACE on every refresh.
_deployed_connections = set()


class StorageProvider:
    def __init__(self, conn=None):
        self.conn = conn

    def is_local_connection(self) -> bool: # Helper to check if the connection is to localhost or local machine.
        if not self.conn:
            return True
        try:
            dsn = str(self.conn.dsn).lower()
            if "localhost" in dsn or "127.0.0.1" in dsn:
                return True
            import socket
            local_hostname = socket.gethostname().lower()
            if local_hostname in dsn:
                return True
        except Exception:
            pass
        return False

    def get_storage_info(self) -> list:
        """
        Retrieve mount point information via SSH using the bundled OCI key.
        """
        if not self.conn:
            return [{"ssh_error": "No database connection available."}]

        try:
            from utils.ssh_mount_provider import get_mounts_via_ssh
            from db_connection import get_config_for_db, load_db_names_api, get_bundled_oci_key

            host = ""
            host_username = ""
            ssh_key_path = None

            # Extract host IP/hostname from the active DB connection DSN
            try:
                dsn_str = str(self.conn.dsn)
                if ":" in dsn_str:
                    host = dsn_str.split(":")[0].strip()
                elif "/" in dsn_str:
                    host = dsn_str.split("/")[0].strip()
                else:
                    host = dsn_str.strip()
            except Exception:
                pass

            # Find the matching DB config from registry to get the host username
            try:
                for db in load_db_names_api():
                    cfg = get_config_for_db(db) or {}
                    cfg_host = cfg.get("host", "").strip()
                    if (host and cfg_host == host) or not host:
                        if not host:
                            host = cfg_host
                        host_username = cfg.get("host_username", "").strip()
                        ssh_key_path = cfg.get("ssh_key_path") or cfg.get("oci_key_path") or cfg.get("key_filename")
                        if host_username:
                            break
            except Exception as e:
                return [{"ssh_error": f"Failed to read registry credentials: {e}"}]

            if not host:
                return [{"ssh_error": "Host address could not be determined from the database connection."}]

            # Default username fallback to 'opc'
            if not host_username:
                host_username = "opc"

            # Resolve which SSH key to use, in priority order:
            #   1. Bundled OCI key shipped with the app (keys/ folder).
            #   2. A key path configured directly in the registry file.
            bundled_key_path = get_bundled_oci_key(host)

            key_file_to_use = None
            if bundled_key_path and os.path.exists(bundled_key_path):
                key_file_to_use = bundled_key_path
            elif ssh_key_path and os.path.exists(ssh_key_path):
                key_file_to_use = ssh_key_path

            if not key_file_to_use:
                return [{"ssh_error": "No OCI key found in the keys/ folder for this host."}]

            result = get_mounts_via_ssh(
                host=host,
                username=host_username,
                key_filename=key_file_to_use
            )

            if result.get("error"):
                # Return the real SSH error so the UI can display it
                return [{"ssh_error": result["error"]}]

            return result.get("volumes", [])

        except Exception as e:
            return [{"ssh_error": f"Unexpected error during mount point retrieval: {e}"}]



    def get_db_file_storage_fallback(self) -> list:
        """
        Fallback when SSH/Java are unavailable.
        Shows real Oracle-level storage areas:
          1. Datafiles directory  — space from DBA_DATA_FILES + DBA_FREE_SPACE
          2. Temp files directory — space from DBA_TEMP_FILES + V$TEMP_SPACE_HEADER
          3. FRA directory        — space from V$RECOVERY_FILE_DEST
          4. Redo/control dir     — location from V$LOGFILE
        """
        try:
            cursor = self.conn.cursor()
            volumes = []

            # ── Helper: GB conversion ─────────────────────────────────────────
            def _gb(b):
                return (b or 0) / (1024 ** 3)

            # ── 1. Datafiles directory + total tablespace space ───────────────
            try:
                cursor.execute("SELECT file_name FROM dba_data_files FETCH FIRST 1 ROWS ONLY")
                row = cursor.fetchone()
                if row:
                    parts = row[0].split("/")
                    # Keep up to 7 path components: /home/oracle/u01/app/oracle/oradata
                    data_root = "/" + "/".join(p for p in parts[1:7] if p)

                    cursor.execute("""
                        SELECT SUM(d.bytes) AS total_b, SUM(NVL(f.free_b, 0)) AS free_b
                        FROM (
                            SELECT tablespace_name, SUM(bytes) AS bytes
                            FROM dba_data_files GROUP BY tablespace_name
                        ) d
                        LEFT JOIN (
                            SELECT tablespace_name, SUM(bytes) AS free_b
                            FROM dba_free_space GROUP BY tablespace_name
                        ) f ON d.tablespace_name = f.tablespace_name
                    """)
                    ts = cursor.fetchone()
                    total_b = ts[0] or 0
                    free_b  = ts[1] or 0
                    used_b  = total_b - free_b
                    pct     = (used_b / total_b * 100) if total_b > 0 else 0
                    volumes.append({
                        "drive":       f"{data_root} (Datafiles)",
                        "mount_point": data_root,
                        "total":       _gb(total_b),
                        "used":        _gb(used_b),
                        "free":        _gb(free_b),
                        "pct":         pct,
                        "fs_type":     "oracle-datafiles",
                        "uuid":        "",
                        "state":       "ACTIVE",
                    })
            except Exception as e:
                print(f"[fallback] datafiles: {e}")

            # ── 2. Temp files directory + temp space ──────────────────────────
            try:
                cursor.execute("SELECT file_name FROM dba_temp_files FETCH FIRST 1 ROWS ONLY")
                row = cursor.fetchone()
                if row:
                    parts = row[0].split("/")
                    temp_root = "/" + "/".join(p for p in parts[1:7] if p)

                    cursor.execute("""
                        SELECT SUM(bytes_max) AS total_b, SUM(bytes_used) AS used_b,
                               SUM(bytes_max - bytes_used) AS free_b
                        FROM v$temp_space_header
                    """)
                    ts = cursor.fetchone()
                    total_b = ts[0] or 0
                    used_b  = ts[1] or 0
                    free_b  = ts[2] or 0
                    pct     = (used_b / total_b * 100) if total_b > 0 else 0
                    # Only add if different root than datafiles
                    if temp_root not in [v["mount_point"] for v in volumes]:
                        volumes.append({
                            "drive":       f"{temp_root} (Temp)",
                            "mount_point": temp_root,
                            "total":       _gb(total_b),
                            "used":        _gb(used_b),
                            "free":        _gb(free_b),
                            "pct":         pct,
                            "fs_type":     "oracle-temp",
                            "uuid":        "",
                            "state":       "ACTIVE",
                        })
            except Exception as e:
                print(f"[fallback] tempfiles: {e}")

            # ── 3. Fast Recovery Area (FRA) from v$recovery_file_dest ─────────
            try:
                cursor.execute("""
                    SELECT name, space_limit, space_used, space_reclaimable
                    FROM v$recovery_file_dest
                """)
                fra = cursor.fetchone()
                if fra and fra[1]:
                    fra_path  = fra[0] or "/fast_recovery_area"
                    fra_limit = fra[1] or 0
                    fra_used  = fra[2] or 0
                    fra_recl  = fra[3] or 0
                    fra_free  = fra_limit - fra_used + fra_recl
                    fra_pct   = (fra_used / fra_limit * 100) if fra_limit > 0 else 0
                    volumes.append({
                        "drive":       f"{fra_path} (FRA/ARC)",
                        "mount_point": fra_path,
                        "total":       _gb(fra_limit),
                        "used":        _gb(fra_used),
                        "free":        _gb(fra_free),
                        "pct":         fra_pct,
                        "fs_type":     "oracle-fra",
                        "uuid":        "",
                        "state":       "ACTIVE",
                    })
            except Exception as e:
                print(f"[fallback] FRA: {e}")

            # ── 4. Redo log directory ─────────────────────────────────────────
            try:
                cursor.execute("SELECT member FROM v$logfile FETCH FIRST 1 ROWS ONLY")
                row = cursor.fetchone()
                if row:
                    parts = row[0].split("/")
                    redo_root = "/" + "/".join(p for p in parts[1:7] if p)
                    if redo_root not in [v["mount_point"] for v in volumes]:
                        volumes.append({
                            "drive":       f"{redo_root} (Redo Logs)",
                            "mount_point": redo_root,
                            "total":       0.0,
                            "used":        0.0,
                            "free":        0.0,
                            "pct":         0.0,
                            "fs_type":     "oracle-redo",
                            "uuid":        "",
                            "state":       "ACTIVE",
                        })
            except Exception as e:
                print(f"[fallback] redo: {e}")

            return volumes

        except Exception as e:
            print(f"Fallback DB file storage query failed: {e}")
            return []


    # ──────────────────────────────────────────────────────────────────────────
    #  Java Bridge – deployed ONCE per connection session (Issue #8, #9 fixed)
    # ──────────────────────────────────────────────────────────────────────────
    def deploy_java_bridge(self):
        """
        Deploys the Java stored procedure to capture host filesystem storage
        details.  Uses a module-level set to avoid redeploying on every refresh.
        """
        global _deployed_connections
        conn_id = id(self.conn)
        if conn_id in _deployed_connections:
            return  # Already deployed for this connection – skip

        cursor = self.conn.cursor()

        # ── Build the Java source as a raw string so we control every byte ──
        # Key escaping rule:
        #   Python triple-quoted string:  "\\\\s+"  →  Java receives:  "\\s+"
        # which Java compiles as the regex \s+ (whitespace).  Using only
        # "\\s+" in Python gives Java "\s+" which is a lexical error (Issue #1).

        java_source = (
            'CREATE OR REPLACE AND RESOLVE JAVA SOURCE NAMED "StorageScanner" AS\n'
            'import java.io.File;\n'
            'import java.io.BufferedReader;\n'
            'import java.io.FileReader;\n'
            'import java.io.InputStreamReader;\n'
            'import java.util.LinkedHashMap;\n'
            'import java.util.Map;\n'
            'import java.util.ArrayList;\n'
            'import java.util.List;\n'
            '\n'
            'public class StorageScanner {\n'
            '    private static long parseSize(String s, boolean isKb) {\n'
            '        if (s == null || s.isEmpty() || s.equals("-")) return 0;\n'
            '        s = s.toUpperCase().replace("B", "");\n'
            '        try {\n'
            '            long factor = isKb ? 1024L : 1L;\n'
            '            if (s.endsWith("K")) return (long)(Double.parseDouble(s.substring(0, s.length()-1)) * 1024L);\n'
            '            if (s.endsWith("M")) return (long)(Double.parseDouble(s.substring(0, s.length()-1)) * 1024L * 1024L);\n'
            '            if (s.endsWith("G")) return (long)(Double.parseDouble(s.substring(0, s.length()-1)) * 1024L * 1024L * 1024L);\n'
            '            if (s.endsWith("T")) return (long)(Double.parseDouble(s.substring(0, s.length()-1)) * 1024L * 1024L * 1024L * 1024L);\n'
            '            if (s.endsWith("P")) return (long)(Double.parseDouble(s.substring(0, s.length()-1)) * 1024L * 1024L * 1024L * 1024L * 1024L);\n'
            '            return (long)(Double.parseDouble(s) * factor);\n'
            '        } catch (Exception e) { return 0; }\n'
            '    }\n'
            '\n'
            '    public static String getStorageInfo() {\n'
            '        StringBuilder sb = new StringBuilder();\n'
            '        try {\n'
            '            String os = System.getProperty("os.name").toLowerCase();\n'
            '\n'
            '            if (!os.contains("win")) {\n'
            '                Map<String, String[]> mounts = new LinkedHashMap<String, String[]>();\n'
            '                try {\n'
            '                    String dfCmd = "df -TkP 2>/dev/null || df -Tk 2>/dev/null || df -P 2>/dev/null";\n'
            '                    Process dp = Runtime.getRuntime().exec(new String[]{"/bin/sh","-c",dfCmd});\n'
            '                    BufferedReader dr = new BufferedReader(new InputStreamReader(dp.getInputStream()));\n'
            '                    String dl;\n'
            '                    boolean hdr = true;\n'
            '                    String pendingLine = null;\n'
            '                    while ((dl = dr.readLine()) != null) {\n'
            '                        if (hdr) { hdr = false; continue; }\n'
            '                        if (pendingLine != null) {\n'
            '                            dl = pendingLine + " " + dl.trim();\n'
            '                            pendingLine = null;\n'
            '                        }\n'
            '                        String[] p = dl.trim().split("\\\\s+");\n'
            '                        if (p.length >= 7) {\n'
            '                            String src = p[0];\n'
            '                            String fst = p[1];\n'
            '                            String totalStr = p[2];\n'
            '                            String usedStr = p[3];\n'
            '                            String freeStr = p[4];\n'
            '                            String tgt = p[p.length-1];\n'
            '                            mounts.put(tgt, new String[]{src, fst, totalStr, usedStr, freeStr});\n'
            '                        } else if (p.length >= 6 && !p[1].matches(\"\\\\d+\")) {\n'
            '                            String src = p[0];\n'
            '                            String fst = p[1];\n'
            '                            String totalStr = p[2];\n'
            '                            String usedStr = p[3];\n'
            '                            String freeStr = p[4];\n'
            '                            String tgt = p[p.length-1];\n'
            '                            mounts.put(tgt, new String[]{src, fst, totalStr, usedStr, freeStr});\n'
            '                        } else if (p.length >= 6) {\n'
            '                            String src = p[0];\n'
            '                            String fst = "unknown";\n'
            '                            String totalStr = p[1];\n'
            '                            String usedStr = p[2];\n'
            '                            String freeStr = p[3];\n'
            '                            String tgt = p[p.length-1];\n'
            '                            mounts.put(tgt, new String[]{src, fst, totalStr, usedStr, freeStr});\n'
            '                        } else if (p.length < 6 && !dl.trim().isEmpty()) {\n'
            '                            pendingLine = dl;\n'
            '                        }\n'
            '                    }\n'
            '                    dr.close(); dp.waitFor();\n'
            '                } catch (Exception ignored) {}\n'
            '\n'
            '                for (Map.Entry<String, String[]> entry : mounts.entrySet()) {\n'
            '                    String tgt  = entry.getKey();\n'
            '                    String[] info = entry.getValue();\n'
            '                    String src  = info[0];\n'
            '                    String fst  = info[1];\n'
            '                    long totalBytes = parseSize(info[2], true);\n'
            '                    long usedBytes  = parseSize(info[3], true);\n'
            '                    long freeBytes  = parseSize(info[4], true);\n'
            '                    \n'
            '                    if (totalBytes <= 0) continue;\n'
            '                    \n'
            '                    String stype = "Linux";\n'
            '                    String fstLower = fst.toLowerCase();\n'
            '                    if (fstLower.equals("tmpfs") || fstLower.equals("devtmpfs") || fstLower.equals("proc") || fstLower.equals("sysfs")) {\n'
            '                        stype = "Pseudo";\n'
            '                    }\n'
            '                    \n'
            '                    sb.append(tgt).append("|")\n'
            '                      .append(src).append("|")\n'
            '                      .append(fst).append("|")\n'
            '                      .append(totalBytes).append("|")\n'
            '                      .append(usedBytes).append("|")\n'
            '                      .append(freeBytes).append("|")\n'
            '                      .append("").append("|")\n'
            '                      .append(stype).append(";");\n'
            '                }\n'
            '                if (sb.length() > 0) return sb.toString();\n'
            '            }\n'
            '\n'
            '            if (os.contains("win")) {\n'
            '                File[] roots = File.listRoots();\n'
            '                if (roots != null) {\n'
            '                    for (File root : roots) {\n'
            '                        long t = root.getTotalSpace();\n'
            '                        long f = root.getFreeSpace();\n'
            '                        long u = t - f;\n'
            '                        sb.append(root.getPath()).append("|")\n'
            '                          .append(root.getPath()).append("|NTFS|")\n'
            '                          .append(t).append("|").append(u).append("|").append(f).append("||Linux;");\n'
            '                    }\n'
            '                }\n'
            '            } else {\n'
            '                File root = new File("/");\n'
            '                long t = root.getTotalSpace();\n'
            '                long f = root.getFreeSpace();\n'
            '                sb.append("/|/|ext4|").append(t).append("|").append(t-f).append("|").append(f).append("||Linux;");\n'
            '            }\n'
            '        } catch (Throwable t) {\n'
            '            return "ERR:" + t.toString();\n'
            '        }\n'
            '        return sb.toString();\n'
            '    }\n'
            '\n'
            '    public static String getProcessInfo() {\n'
            '        StringBuilder sb = new StringBuilder();\n'
            '        try {\n'
            '            String os = System.getProperty("os.name").toLowerCase();\n'
            '            Process p;\n'
            '            if (os.contains("win")) {\n'
            '                // Windows: Use tasklist or powershell, tasklist is simpler to parse\n'
            '                p = Runtime.getRuntime().exec(new String[]{"cmd.exe", "/c", "tasklist /FO CSV /NH"});\n'
            '            } else {\n'
            '                // Linux/MacOS: Use ps to fetch PID, %CPU, %MEM, and COMMAND for oracle processes\n'
            '                String cmd = "ps -eo pid,pcpu,pmem,args | grep -i oracle | grep -v grep | head -n 50";\n'
            '                p = Runtime.getRuntime().exec(new String[]{"/bin/sh", "-c", cmd});\n'
            '            }\n'
            '            BufferedReader reader = new BufferedReader(new InputStreamReader(p.getInputStream()));\n'
            '            String line;\n'
            '            while ((line = reader.readLine()) != null) {\n'
            '                if (line.trim().isEmpty()) continue;\n'
            '                if (os.contains("win")) {\n'
            '                    // Format: "Image Name","PID","Session Name","Session#","Mem Usage"\n'
            '                    String[] parts = line.split("\",\"");\n'
            '                    if (parts.length >= 5) {\n'
            '                        String name = parts[0].replace("\\"", "").trim();\n'
            '                        if (name.toLowerCase().contains("oracle")) {\n'
            '                            String pid = parts[1].replace("\\"", "").trim();\n'
            '                            String memStr = parts[4].replace("\\"", "").replace("K", "").replace("M", "").replace(" ", "").trim();\n'
            '                            double memKb = 0;\n'
            '                            try { memKb = Double.parseDouble(memStr); } catch(Exception e){}\n'
            '                            // tasklist doesn\'t give %CPU easily, default to 0.0 or random small, let\'s output format: PID|CPU%|MEM_MB\n'
            '                            sb.append(pid).append("|0.0|").append(Math.round(memKb/1024.0)).append(";");\n'
            '                        }\n'
            '                    }\n'
            '                } else {\n'
            '                    // Linux format: PID %CPU %MEM ARGS\n'
            '                    String[] parts = line.trim().split("\\\\s+", 4);\n'
            '                    if (parts.length >= 3) {\n'
            '                        String pid = parts[0];\n'
            '                        String cpu = parts[1];\n'
            '                        String memPct = parts[2];\n'
            '                        // Estimate RES in MB based on total memory if possible, or just send memory percentage\n'
            '                        // We\'ll return format: PID|CPU%|MEM_PCT%\n'
            '                        sb.append(pid).append("|").append(cpu).append("|").append(memPct).append(";");\n'
            '                    }\n'
            '                }\n'
            '            }\n'
            '            reader.close(); p.waitFor();\n'
            '        } catch (Throwable t) {\n'
            '            return "ERR:" + t.toString();\n'
            '        }\n'
            '        return sb.toString();\n'
            '    }\n'
            '}\n'
        )

        cursor.execute(java_source)

        # Grant permissions (once per deploy)
        schema = self.conn.username
        try:
            cursor.execute(f"""
                BEGIN
                    dbms_java.grant_permission('{schema.upper()}', 'SYS:java.lang.RuntimePermission', 'getFileSystemAttributes', '');
                    dbms_java.grant_permission('{schema.upper()}', 'SYS:java.io.FilePermission', '<<ALL FILES>>', 'read,execute');
                    dbms_java.grant_permission('{schema.upper()}', 'SYS:java.lang.RuntimePermission', 'writeFileDescriptor', '');
                    dbms_java.grant_permission('{schema.upper()}', 'SYS:java.lang.RuntimePermission', 'readFileDescriptor', '');
                END;
            """)
        except Exception as e:
            print(f"Non-critical warning: unable to grant file permissions: {e}")

        # Create PL/SQL wrapper for storage
        cursor.execute("""
CREATE OR REPLACE FUNCTION get_host_storage_info RETURN VARCHAR2 AS
LANGUAGE JAVA NAME 'StorageScanner.getStorageInfo() return java.lang.String';
        """)

        # Create PL/SQL wrapper for processes
        cursor.execute("""
CREATE OR REPLACE FUNCTION get_host_process_info RETURN VARCHAR2 AS
LANGUAGE JAVA NAME 'StorageScanner.getProcessInfo() return java.lang.String';
        """)

        _deployed_connections.add(conn_id)


    def get_process_info(self, limit: int = 10) -> list:
        """
        Returns top 10 running processes by CPU usage using psutil (local) or SSH Paramiko (remote server).
        Filters out Oracle background processes (ora_...), returning only Oracle user and system processes.
        [ {"pid": str, "name": str, "cpu": float, "mem": float, "username": str} ]
        """
        def is_ora_background(proc_name: str) -> bool:
            if not proc_name:
                return False
            name_lower = proc_name.lower().strip()
            return name_lower.startswith("ora_")

        # ── 1. Try SSH via Paramiko for remote servers ────────────────────────
        if self.conn:
            try:
                dsn_str = str(self.conn.dsn)
                host = dsn_str.split(":")[0].strip()
                username, password = "", ""

                try:
                    from db_connection import get_config_for_db, get_db_names
                    for db in get_db_names():
                        cfg = get_config_for_db(db)
                        if cfg and cfg.get("host", "").strip() == host:
                            username = cfg.get("host_username", "").strip()
                            password = cfg.get("host_password", "").strip()
                            break
                except Exception:
                    pass

                if host and username:
                    import paramiko
                    client = paramiko.SSHClient()
                    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    client.connect(
                        hostname=host, username=username, password=password,
                        timeout=5, look_for_keys=False, allow_agent=False
                    )
                    # Get top 100 processes from remote OS to allow filtering
                    cmd = "ps -eo pid,user,pcpu,pmem,args --sort=-%cpu | head -n 100"
                    _, stdout, _ = client.exec_command(cmd, timeout=10)
                    raw = stdout.read().decode(errors="replace").strip().splitlines()
                    client.close()

                    processes = []
                    for line in raw[1:]:  # skip header
                        if not line.strip():
                            continue
                        parts = line.strip().split(None, 4)
                        if len(parts) >= 5:
                            pid_str, user_str, cpu_str, mem_str = parts[0], parts[1], parts[2], parts[3]
                            args_str = parts[4].strip()
                            
                            # Filter out system and root users
                            if user_str.lower() in ("root", "sys", "system", "daemon"):
                                continue

                            # Filter out Oracle background processes (e.g. ora_pmon_ORCL)
                            if is_ora_background(args_str):
                                continue

                            comm_str = args_str
                            if args_str.startswith("oracle"):
                                first_word = args_str.split()[0]
                                sid = first_word[6:] # extract ORCL from oracleORCL
                                if sid:
                                    user_str = f"{user_str} ({sid})"
                            
                            try:
                                processes.append({
                                    "pid":      pid_str,
                                    "name":     comm_str,
                                    "username": user_str,
                                    "cpu":      round(float(cpu_str), 2),
                                    "mem":      round(float(mem_str), 2),
                                })
                            except (ValueError, IndexError):
                                continue
                            if len(processes) >= limit:
                                break
                    if processes:
                        return processes
            except Exception as e:
                print(f"[StorageProvider] SSH process scanner failed: {e}")

        # ── 2. psutil — top 10 by CPU (local OS fallback) ───────────────────
        return self.get_fallback_processes(limit=limit)

    def get_fallback_processes(self, limit: int = 10) -> list:
        """
        Top 10 running processes by OS using psutil.
        Filters out Oracle background processes (ora_...), returning Oracle user and system processes only.
        """
        def is_ora_background(proc_name: str) -> bool:
            if not proc_name:
                return False
            name_lower = proc_name.lower().strip()
            return name_lower.startswith("ora_")

        import psutil
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_percent', 'cmdline']):
            try:
                info = proc.info
                cmdline = info.get('cmdline') or []
                args_str = " ".join(cmdline) if cmdline else (info.get('name') or '')
                user_str = info.get('username') or ''
                
                # Exclude root/sys/system
                if user_str.lower() in ("root", "sys", "system", "daemon"):
                    continue

                if is_ora_background(args_str):
                    continue

                comm_str = args_str
                if args_str.startswith("oracle"):
                    first_word = args_str.split()[0]
                    sid = first_word[6:]
                    if sid:
                        user_str = f"{user_str} ({sid})"
                        
                processes.append({
                    "pid":      str(info.get('pid', '')),
                    "name":     comm_str,
                    "username": user_str,
                    "cpu_percent": info.get('cpu_percent') or 0.0,
                    "memory_percent": info.get('memory_percent') or 0.0,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Sort by CPU percent descending and return top N
        top_cpu = sorted(
            processes,
            key=lambda x: x.get('cpu_percent') or 0.0,
            reverse=True
        )[:limit]

        result = []
        for p in top_cpu:
            result.append({
                "pid":      p['pid'],
                "name":     p['name'],
                "username": p['username'],
                "cpu":      round(float(p['cpu_percent']), 2),
                "mem":      round(float(p['memory_percent']), 2),
            })
        return result

    # ──────────────────────────────────────────────────────────────────────────
    #  Response parser  –  format:  mount|device|fstype|total|used|free|uuid|stype;
    # ──────────────────────────────────────────────────────────────────────────
    def _parse_java_response(self, response: str) -> list:

        volumes = []
        for item in response.split(";"):
            item = item.strip()
            if not item:
                continue
            parts = item.split("|")
            if len(parts) < 6:
                continue
            mount_point = parts[0]
            device      = parts[1]
            fs_type     = parts[2]
            try:
                total_bytes = int(parts[3])
                used_bytes  = int(parts[4])
                free_bytes  = int(parts[5])
                uuid        = parts[6] if len(parts) >= 7 else ""
                stype       = parts[7] if len(parts) >= 8 else "Linux"
            except ValueError:
                continue

            total_gb = total_bytes / (1024**3)
            used_gb  = used_bytes  / (1024**3)
            free_gb  = free_bytes  / (1024**3)
            pct      = (used_bytes / total_bytes) * 100 if total_bytes > 0 else 0

            # Normalise drive label
            drive_label = device if device else mount_point
            if drive_label.endswith("\\") and len(drive_label) > 3:
                drive_label = drive_label[:-1]
            elif drive_label.endswith("/") and len(drive_label) > 1:
                drive_label = drive_label[:-1]

            volumes.append({
                "drive":       drive_label,
                "mount_point": mount_point,
                "total":       total_gb,
                "used":        used_gb,
                "free":        free_gb,
                "pct":         pct,
                "fs_type":     fs_type,
                "uuid":        uuid,
                "stype":       stype,
                "state":       "ACTIVE",
            })
        return volumes

    def get_fallback_storage(self) -> list:
        raise NotImplementedError("Subclasses must implement fallback storage scanning.")


# ──────────────────────────────────────────────────────────────────────────────
#  Platform-specific local fallback providers
# ──────────────────────────────────────────────────────────────────────────────

class WindowsStorageProvider(StorageProvider):
    def get_fallback_storage(self) -> list:
        volumes = []
        try:
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for letter in string.ascii_uppercase:
                if bitmask & (1 << (ord(letter) - 65)):
                    drive_path = f"{letter}:\\"
                    try:
                        total, used, free = shutil.disk_usage(drive_path)
                        total_gb = total / (1024**3)
                        used_gb  = used  / (1024**3)
                        free_gb  = free  / (1024**3)
                        pct      = (used / total) * 100 if total > 0 else 0
                        volumes.append({
                            "drive":       f"{letter}:",
                            "mount_point": drive_path,
                            "total": total_gb, "used": used_gb, "free": free_gb,
                            "pct": pct, "fs_type": "NTFS", "uuid": "", "stype": "Linux",
                        })
                    except Exception:
                        volumes.append({
                            "drive":       f"{letter}:",
                            "mount_point": drive_path,
                            "total": 0.0, "used": 0.0, "free": 0.0,
                            "pct": 0.0, "fs_type": "NTFS", "uuid": "", "stype": "Linux",
                        })
        except Exception as e:
            print(f"Windows fallback storage check failed: {e}")
        return volumes


class LinuxStorageProvider(StorageProvider):
    def get_fallback_storage(self) -> list:
        PSEUDO = {"proc","sysfs","devtmpfs","tmpfs","cgroup","cgroup2","autofs",
                  "configfs","debugfs","hugetlbfs","mqueue","pstore","securityfs",
                  "devpts","binfmt_misc","fusectl","overlay","aufs"}
        volumes = []
        mount_points = {"/": "ext4"}   # ordered dict equivalent
        try:
            if os.path.exists("/proc/mounts"):
                with open("/proc/mounts", "r") as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 3:
                            mount, fs_type = parts[1], parts[2]
                            mount_points[mount] = fs_type
        except Exception:
            pass

        for mount, fs_type in mount_points.items():
            stype = "Pseudo" if fs_type in PSEUDO else "Linux"
            try:
                if not os.path.isdir(mount):
                    continue
                total, used, free = shutil.disk_usage(mount)
                total_gb = total / (1024**3)
                used_gb  = used  / (1024**3)
                free_gb  = free  / (1024**3)
                pct      = (used / total) * 100 if total > 0 else 0
                volumes.append({
                    "drive":       mount,
                    "mount_point": mount,
                    "total": total_gb, "used": used_gb, "free": free_gb,
                    "pct": pct, "fs_type": fs_type, "uuid": "", "stype": stype,
                })
            except Exception:
                volumes.append({
                    "drive":       mount,
                    "mount_point": mount,
                    "total": 0.0, "used": 0.0, "free": 0.0,
                    "pct": 0.0, "fs_type": fs_type, "uuid": "", "stype": stype,
                })
        return volumes


class MacOSStorageProvider(StorageProvider):
    def get_fallback_storage(self) -> list:
        volumes = []
        mount_points = {"/": "apfs", "/System": "apfs"}
        try:
            volumes_dir = "/Volumes"
            if os.path.exists(volumes_dir) and os.path.isdir(volumes_dir):
                for item in os.listdir(volumes_dir):
                    full_path = os.path.join(volumes_dir, item)
                    if os.path.ismount(full_path) or os.path.isdir(full_path):
                        if full_path not in mount_points:
                            mount_points[full_path] = "hfs"
        except Exception:
            pass

        for mount, fs_type in mount_points.items():
            try:
                total, used, free = shutil.disk_usage(mount)
                total_gb = total / (1024**3)
                used_gb  = used  / (1024**3)
                free_gb  = free  / (1024**3)
                pct      = (used / total) * 100 if total > 0 else 0
                volumes.append({
                    "drive":       mount,
                    "mount_point": mount,
                    "total": total_gb, "used": used_gb, "free": free_gb,
                    "pct": pct, "fs_type": fs_type, "uuid": "", "stype": "Linux",
                })
            except Exception:
                volumes.append({
                    "drive":       mount,
                    "mount_point": mount,
                    "total": 0.0, "used": 0.0, "free": 0.0,
                    "pct": 0.0, "fs_type": fs_type, "uuid": "", "stype": "Linux",
                })
        return volumes


# ──────────────────────────────────────────────────────────────────────────────
#  Factory
# ──────────────────────────────────────────────────────────────────────────────

def get_storage_provider(conn=None) -> StorageProvider:
    """Detects host platform and returns the appropriate StorageProvider."""
    os_name = "Linux"  # Default
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT PLATFORM_NAME FROM V$DATABASE")
            plat = cursor.fetchone()
            if plat and plat[0]:
                plat_str = str(plat[0]).lower()
                if "windows" in plat_str:
                    os_name = "Windows"
                elif "linux" in plat_str:
                    os_name = "Linux"
                elif "darwin" in plat_str or "mac" in plat_str:
                    os_name = "MacOS"
                else:
                    os_name = "Linux"
                return _instantiate_provider(os_name, conn)
        except Exception:
            pass

    local_sys = platform.system()
    if local_sys == "Windows":
        os_name = "Windows"
    elif local_sys == "Darwin":
        os_name = "MacOS"
    else:
        os_name = "Linux"

    return _instantiate_provider(os_name, conn)


def _instantiate_provider(os_name: str, conn) -> StorageProvider:
    if os_name == "Windows":
        return WindowsStorageProvider(conn)
    elif os_name == "MacOS":
        return MacOSStorageProvider(conn)
    return LinuxStorageProvider(conn)
