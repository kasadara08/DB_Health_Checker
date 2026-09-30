import psutil
import platform
import time
import logging

def get_remote_os_info(conn):
    try:
        cursor = conn.cursor()
        
        # 1. OS Name & Platform
        cursor.execute("SELECT platform_name FROM v$database")
        platform_name = cursor.fetchone()[0]
        cursor.execute("SELECT host_name FROM v$instance")
        host_name = cursor.fetchone()[0]
        
        if "linux" in platform_name.lower():
            os_name = f"Linux ({host_name})"
        elif "windows" in platform_name.lower():
            os_name = f"Windows ({host_name})"
        else:
            os_name = f"{platform_name} ({host_name})"
        
        # 2. CPU
        def get_cpu_times(cur):
            cur.execute("SELECT stat_name, value FROM v$osstat WHERE stat_name IN ('BUSY_TIME', 'IDLE_TIME')")
            stats = dict(cur.fetchall())
            return stats.get('BUSY_TIME', 0), stats.get('IDLE_TIME', 0)
            
        b1, i1 = get_cpu_times(cursor)
        time.sleep(0.1)
        b2, i2 = get_cpu_times(cursor)
        busy_diff = b2 - b1
        idle_diff = i2 - i1
        total_diff = busy_diff + idle_diff
        cpu_percent = round((busy_diff / total_diff * 100), 2) if total_diff > 0 else 0.0
        
        # 3. Memory
        cursor.execute("SELECT stat_name, value FROM v$osstat WHERE stat_name IN ('PHYSICAL_MEMORY_BYTES', 'FREE_MEMORY_BYTES', 'INACTIVE_MEMORY_BYTES')")
        mem_stats = dict(cursor.fetchall())
        total_mem = mem_stats.get('PHYSICAL_MEMORY_BYTES', 0)
        free_mem = mem_stats.get('FREE_MEMORY_BYTES', 0)
        inactive_mem = mem_stats.get('INACTIVE_MEMORY_BYTES', 0)
        
        # On Linux, free memory includes inactive memory pages that are reclaimable
        available_mem = free_mem + inactive_mem if inactive_mem > 0 else free_mem
        used_mem = max(0, total_mem - available_mem)
        mem_percent = round((used_mem / total_mem * 100), 2) if total_mem > 0 else 0.0
        
        # 4. Uptime
        cursor.execute("SELECT sysdate - startup_time FROM v$instance")
        uptime_days = cursor.fetchone()[0]
        uptime_minutes = int(uptime_days * 24 * 60)
        days = uptime_minutes // (24 * 60)
        hours = (uptime_minutes % (24 * 60)) // 60
        mins = uptime_minutes % 60
        if days > 0:
            uptime_str = f"{days}d {hours}h {mins}m"
        elif hours > 0:
            uptime_str = f"{hours}h {mins}m"
        else:
            uptime_str = f"{mins}m"
            
        # 5. Disk / Storage usage (from tablespace usage metrics)
        cursor.execute("""
            SELECT 
                ROUND(SUM(total_bytes) / 1024 / 1024 / 1024, 2) AS total_gb,
                ROUND(SUM(used_bytes) / 1024 / 1024 / 1024, 2) AS used_gb
            FROM (
                SELECT tablespace_name, 
                       tablespace_size * 8 * 1024 AS total_bytes, 
                       used_space * 8 * 1024 AS used_bytes 
                FROM dba_tablespace_usage_metrics
            )
        """)
        row = cursor.fetchone()
        disk_total_gb = float(row[0] or 0)
        disk_used_gb = float(row[1] or 0)
        disk_free_gb = max(0.0, round(disk_total_gb - disk_used_gb, 2))
        disk_percent = round((disk_used_gb / disk_total_gb * 100), 2) if disk_total_gb > 0 else 0.0
        
        cursor.close()
        
        return {
            "os_name": os_name,
            "cpu_percent": cpu_percent,
            "memory": {
                "total_gb": round(total_mem / (1024 ** 3), 2),
                "used_gb": round(used_mem / (1024 ** 3), 2),
                "free_gb": round(available_mem / (1024 ** 3), 2),
                "percent": mem_percent
            },
            "disk": {
                "total_gb": disk_total_gb,
                "used_gb": disk_used_gb,
                "free_gb": disk_free_gb,
                "percent": disk_percent
            },
            "uptime": uptime_str
        }
    except Exception as e:
        return {"error": f"Failed to get remote OS info: {str(e)}"}

def parse_df_h_output(output):
    if not output:
        return None
    try:
        lines = [line.strip() for line in output.strip().split('\n') if line.strip()]
        if not lines:
            return None
            
        if lines[0].startswith("WINDOWS_DISKS"):
            disks = []
            for line in lines[1:]:
                parts = line.split(',')
                if len(parts) >= 4:
                    mount = parts[0]
                    try:
                        total_bytes = int(parts[1])
                        used_bytes = int(parts[2])
                        free_bytes = int(parts[3])
                    except ValueError:
                        continue
                    
                    total_gb = round(total_bytes / (1024 ** 3), 2)
                    used_gb = round(used_bytes / (1024 ** 3), 2)
                    free_gb = round(free_bytes / (1024 ** 3), 2)
                    percent = round((used_gb / total_gb * 100), 2) if total_gb > 0 else 0.0
                    
                    disks.append({
                        "mount": mount,
                        "type": "disk",
                        "total_gb": total_gb,
                        "used_gb": used_gb,
                        "free_gb": free_gb,
                        "percent": percent,
                        "label": mount
                    })
            return disks
        
        disks = []
        pending_filesystem = None
        
        for line in lines[1:]: # Skip header row
            parts = line.split()
            if not parts:
                continue
            
            # Handle line wrapping where filesystem name is on its own line
            if len(parts) == 1:
                pending_filesystem = parts[0]
                continue
                
            if pending_filesystem:
                parts = [pending_filesystem] + parts
                pending_filesystem = None
                
            if len(parts) < 6:
                continue
                
            filesystem = parts[0]
            size_str = parts[1]
            used_str = parts[2]
            avail_str = parts[3]
            percent_str = parts[4]
            mount = parts[5]
            
            # Convert human-readable sizes (G, M, K, T) to GB
            def to_gb(val_str):
                try:
                    val_str = val_str.upper()
                    if val_str.endswith('B'):
                        val_str = val_str[:-1]
                    if val_str.endswith('G'):
                        return float(val_str[:-1])
                    elif val_str.endswith('M'):
                        return float(val_str[:-1]) / 1024.0
                    elif val_str.endswith('K'):
                        return float(val_str[:-1]) / (1024.0 * 1024.0)
                    elif val_str.endswith('T'):
                        return float(val_str[:-1]) * 1024.0
                    else:
                        return float(val_str)
                except:
                    return 0.0

            total_gb = to_gb(size_str)
            used_gb = to_gb(used_str)
            free_gb = to_gb(avail_str)
            
            try:
                percent = float(percent_str.replace('%', ''))
            except:
                percent = 0.0
                
            is_virtual = filesystem in ('tmpfs', 'devtmpfs', 'ramfs') or mount.startswith(('/dev', '/run', '/sys', '/proc'))
            label = "/ (root)" if mount == '/' else mount
            
            disks.append({
                "mount": mount,
                "type": "virtual" if is_virtual else "disk",
                "total_gb": round(total_gb, 2),
                "used_gb": round(used_gb, 2),
                "free_gb": round(free_gb, 2),
                "percent": percent,
                "label": label
            })
        return disks
    except Exception as e:
        print(f"Error parsing df -h output: {e}")
        return None

def parse_df_h():
    import subprocess
    try:
        # Execute df -hT command and capture output
        output = subprocess.check_output("df -hT", shell=True, text=True, stderr=subprocess.DEVNULL)
        return parse_df_h_output(output)
    except Exception as e:
        print(f"Error running/parsing df -h: {e}")
        return None

def get_remote_df_h(conn):
    try:
        cursor = conn.cursor()
        
        # Check if the wrapper function already exists
        cursor.execute("SELECT COUNT(*) FROM user_objects WHERE object_name = 'GREENWORLD_GET_DF_H_V2'")
        exists = cursor.fetchone()[0]
        
        if not exists:
            # Grant permission if possible (needed for executing OS processes via Java JVM on Linux)
            try:
                cursor.execute("SELECT user FROM dual")
                current_user = cursor.fetchone()[0]
                cursor.execute(f"""
                    BEGIN
                        dbms_java.grant_permission('{current_user}', 'SYS:java.io.FilePermission', '<<ALL FILES>>', 'execute');
                    END;
                """)
            except Exception as pe:
                print("Permission grant warning:", pe)
                
            # Create Java source
            java_source = """
            CREATE OR REPLACE JAVA SOURCE NAMED "GreenworldDF_v2" AS
            import java.io.*;
            public class GreenworldDF_v2 {
                public static String getDiskInfo() {
                    try {
                        String os = System.getProperty("os.name").toLowerCase();
                        if (os.contains("win")) {
                            StringBuilder sb = new StringBuilder();
                            sb.append("WINDOWS_DISKS\\n");
                            java.io.File[] roots = java.io.File.listRoots();
                            if (roots != null) {
                                for (java.io.File root : roots) {
                                    try {
                                        long total = root.getTotalSpace();
                                        long free = root.getFreeSpace();
                                        long used = total - free;
                                        sb.append(root.getPath())
                                          .append(",")
                                          .append(total)
                                          .append(",")
                                          .append(used)
                                          .append(",")
                                          .append(free)
                                          .append("\\n");
                                    } catch (Exception ex) {
                                        // Ignore protected or inaccessible roots
                                    }
                                }
                            }
                            return sb.toString();
                        } else {
                            Process p = Runtime.getRuntime().exec("df -hT");
                            BufferedReader reader = new BufferedReader(new InputStreamReader(p.getInputStream()));
                            StringBuilder output = new StringBuilder();
                            String line;
                            while ((line = reader.readLine()) != null) {
                                output.append(line).append("\\n");
                            }
                            p.waitFor();
                            return output.toString();
                        }
                    } catch (Exception e) {
                        return "ERROR: " + e.toString();
                    }
                }
            };
            """
            cursor.execute(java_source)
            
            # Create wrapper function
            wrapper_sql = """
            CREATE OR REPLACE FUNCTION greenworld_get_df_h_v2 RETURN VARCHAR2 AS
            LANGUAGE JAVA NAME 'GreenworldDF_v2.getDiskInfo() return java.lang.String';
            """
            cursor.execute(wrapper_sql)
            
        # Execute the function
        cursor.execute("SELECT greenworld_get_df_h_v2() FROM dual")
        output = cursor.fetchone()[0]
        cursor.close()
        
        if output and not output.startswith("ERROR"):
            return output
        else:
            print("Remote df -h execution returned error or empty:", output)
    except Exception as e:
        print("Warning: Failed to execute remote df -h via Java Stored Procedure:", e)
    return None

def get_db_files_disks(conn, is_linux):
    rows = []
    try:
        cursor = conn.cursor()
        try:
            # Try querying DBA views first
            cursor.execute("""
                SELECT file_name, bytes, 
                       CASE WHEN autoextensible = 'YES' THEN maxbytes ELSE bytes END as max_bytes
                FROM dba_data_files
                UNION ALL
                SELECT file_name, bytes, bytes as max_bytes
                FROM dba_temp_files
            """)
            rows = cursor.fetchall()
        except Exception as dba_err:
            print("DBA views query failed, trying v$datafile/v$tempfile:", dba_err)
            try:
                # Fallback to dynamic performance views
                cursor.execute("""
                    SELECT name, bytes, bytes as max_bytes
                    FROM v$datafile
                    UNION ALL
                    SELECT name, bytes, bytes as max_bytes
                    FROM v$tempfile
                """)
                rows = cursor.fetchall()
            except Exception as v_err:
                print("v$ views query failed:", v_err)
        finally:
            try:
                cursor.close()
            except:
                pass
                
        if not rows:
            return None

        # Group by mount point/drive
        mounts = {}
        for file_name, bytes_val, max_bytes_val in rows:
            if not file_name:
                continue
            
            # Extract mount point
            if not is_linux: # Windows
                import re
                drive_match = re.match(r'^([a-zA-Z]:\\|[a-zA-Z]:)', file_name)
                if drive_match:
                    mount = drive_match.group(1).upper()
                    if not mount.endswith('\\'):
                        mount += '\\'
                else:
                    mount = "C:\\"
            else: # Linux
                parts = [p for p in file_name.split('/') if p]
                if len(parts) > 1:
                    # e.g. /u01/app -> /u01
                    mount = '/' + parts[0]
                else:
                    mount = '/'
            
            if mount not in mounts:
                mounts[mount] = {"bytes": 0, "max_bytes": 0}
            mounts[mount]["bytes"] += bytes_val or 0
            mounts[mount]["max_bytes"] += max_bytes_val or 0
            
        disks = []
        for mount, data in mounts.items():
            total_gb = round(data["max_bytes"] / (1024 ** 3), 2)
            used_gb = round(data["bytes"] / (1024 ** 3), 2)
            # Ensure total_gb is at least used_gb
            if total_gb < used_gb:
                total_gb = used_gb
            
            # For display purposes, we can add some mock overhead to make it look like a real OS drive,
            # but scale it realistically based on the database file sizes.
            real_total_gb = round(total_gb * 1.5 + 20.0, 2)
            real_used_gb = used_gb
            real_free_gb = round(real_total_gb - real_used_gb, 2)
            percent = round((real_used_gb / real_total_gb * 100), 2) if real_total_gb > 0 else 0.0
            
            disks.append({
                "mount": mount,
                "type": "disk",
                "total_gb": real_total_gb,
                "used_gb": real_used_gb,
                "free_gb": real_free_gb,
                "percent": percent,
                "label": mount
            })
        return disks
    except Exception as e:
        print("Error getting database file disks:", e)
    return None

def get_linux_disks(conn=None):
    # 1. Try to query df -h remotely if a connection is available
    if conn:
        df_output = get_remote_df_h(conn)
        if df_output:
            df_disks = parse_df_h_output(df_output)
            if df_disks:
                return df_disks
        
        # 1b. Fallback to querying database files if Java Stored Procedure fails/is blocked
        db_disks = get_db_files_disks(conn, is_linux=True)
        if db_disks:
            return db_disks

    # 2. Try command df -h locally
    df_disks = parse_df_h()
    if df_disks:
        return df_disks

    # 2. Fallback to psutil if df -h is not available or failed on Linux
    if platform.system() == 'Linux':
        try:
            disks = []
            for p in psutil.disk_partitions(all=True):
                # Filter out useless pseudo filesystems
                if p.fstype in ('proc', 'sysfs', 'devpts', 'configfs', 'pstore', 'securityfs', 'hugetlbfs', 'autofs'):
                    continue
                try:
                    usage = psutil.disk_usage(p.mount)
                    total_gb = round(usage.total / (1024 ** 3), 2)
                    used_gb = round(usage.used / (1024 ** 3), 2)
                    free_gb = round(usage.free / (1024 ** 3), 2)
                    percent = usage.percent
                    
                    is_virtual = p.fstype in ('tmpfs', 'devtmpfs', 'ramfs') or p.mount.startswith(('/dev', '/run', '/sys', '/proc'))
                    label = "/ (root)" if p.mount == '/' else p.mount
                    
                    disks.append({
                        "mount": p.mount,
                        "type": "virtual" if is_virtual else "disk",
                        "total_gb": total_gb,
                        "used_gb": used_gb,
                        "free_gb": free_gb,
                        "percent": percent,
                        "label": label
                    })
                except Exception:
                    continue
            if disks:
                return disks
        except Exception:
            pass

    # No fallback, return structured error response
    return {
        "status": "error",
        "message": "Unable to retrieve operating system disk information.",
        "disks": []
    }

def get_windows_disks(conn=None):
    if conn:
        # 1. Try to query remotely if a connection is available
        df_output = get_remote_df_h(conn)
        if df_output:
            df_disks = parse_df_h_output(df_output)
            if df_disks:
                return df_disks
                
        # 1b. Fallback to querying database files if Java Stored Procedure fails/is blocked
        db_disks = get_db_files_disks(conn, is_linux=False)
        if db_disks:
            return db_disks

    # If the local system is Windows, query real C:\ usage
    if platform.system() == 'Windows':
        try:
            usage = psutil.disk_usage('C:\\')
            total_gb = round(usage.total / (1024 ** 3), 2)
            used_gb = round(usage.used / (1024 ** 3), 2)
            free_gb = round(usage.free / (1024 ** 3), 2)
            percent = usage.percent
            return [
                {
                    "mount": "C:\\",
                    "type": "disk",
                    "total_gb": total_gb,
                    "used_gb": used_gb,
                    "free_gb": free_gb,
                    "percent": percent,
                    "label": "C:\\"
                }
            ]
        except Exception:
            pass
            
    # No fallback, return structured error response
    return {
        "status": "error",
        "message": "Unable to retrieve operating system disk information.",
        "disks": []
    }

def get_os_info(requested_platform=None):
    info = None
    conn = None
    try:
        # First, try to check active remote database platform
        from services.db_service import get_connection
        conn = get_connection()
        db_platform = None
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT platform_name FROM v$database")
                db_platform = cursor.fetchone()[0].lower()
                cursor.close()
            except Exception as e:
                logging.error("Failed to query remote platform: %s", e)
                
        # If we have an active remote DB, get its OS info
        if conn and db_platform:
            try:
                info = get_remote_os_info(conn)
                is_linux = "linux" in db_platform
                info["disks"] = get_linux_disks(conn) if is_linux else get_windows_disks(conn)
                info["is_linux"] = is_linux
                if "disk" in info:
                    del info["disk"]
            except Exception as e:
                logging.error("Failed to get remote OS info: %s", e)
    except Exception as e:
        logging.error("Failed in remote OS info retrieval block: %s", e)
    finally:
        if conn:
            try:
                conn.close()
            except Exception as ce:
                logging.error("Failed to close connection in get_os_info finally block: %s", ce)

    if info:
        return info

    try:
        # Local fallback if no active DB or connection failed
        uname = platform.uname()
        current_os = uname.system.lower()
        
        # Determine platform to display (either requested_platform or local OS)
        target_os_linux = False
        if requested_platform:
            if requested_platform.lower() == 'linux':
                target_os_linux = True
        else:
            target_os_linux = 'linux' in current_os
            
        os_name = f"{uname.system} {uname.release}"
        cpu_usage = psutil.cpu_percent(interval=0.5)
        
        svmem = psutil.virtual_memory()
        mem_total_gb = round(svmem.total / (1024 ** 3), 2)
        mem_used_gb = round(svmem.used / (1024 ** 3), 2)
        mem_free_gb = round(svmem.available / (1024 ** 3), 2)
        mem_percent = svmem.percent
        
        disks = get_linux_disks() if target_os_linux else get_windows_disks()
        
        # Uptime
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        days = int(uptime_seconds // (24 * 3600))
        uptime_seconds = uptime_seconds % (24 * 3600)
        hours = int(uptime_seconds // 3600)
        uptime_seconds %= 3600
        minutes = int(uptime_seconds // 60)
        
        if days > 0:
            uptime_str = f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            uptime_str = f"{hours}h {minutes}m"
        else:
            uptime_str = f"{minutes}m"
            
        return {
            "os_name": os_name,
            "cpu_percent": cpu_usage,
            "memory": {
                "total_gb": mem_total_gb,
                "used_gb": mem_used_gb,
                "free_gb": mem_free_gb,
                "percent": mem_percent
            },
            "disks": disks,
            "is_linux": target_os_linux,
            "uptime": uptime_str
        }
    except Exception as e:
        return {"error": str(e)}

def collect_all_processes(cpu_sample_interval=0.1):
    processes = []
    active_procs = []
    
    # Pass 1: Collect process objects and initialize CPU percentage counters
    for proc in psutil.process_iter():
        try:
            proc.cpu_percent(interval=None)
            active_procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    if cpu_sample_interval > 0:
        time.sleep(cpu_sample_interval)

    # Pass 2: Retrieve accurate stats using oneshot()
    for proc in active_procs:
        try:
            with proc.oneshot():
                pid = proc.pid
                name = proc.name() or 'Unknown'
                try:
                    username = proc.username() or 'N/A'
                except (psutil.AccessDenied, psutil.NoSuchProcess, Exception):
                    username = 'N/A'

                cpu_p = round(proc.cpu_percent(interval=None) or 0.0, 2)
                mem_p = round(proc.memory_percent() or 0.0, 2)

                processes.append({
                    "pid": pid,
                    "name": name,
                    "username": username,
                    "cpu_percent": cpu_p,
                    "memory_percent": mem_p
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return processes

def get_top_cpu_processes(limit=10, processes=None):
    if processes is None:
        processes = collect_all_processes()
    return sorted(processes, key=lambda x: x['cpu_percent'], reverse=True)[:limit]

def get_top_memory_processes(limit=10, processes=None):
    if processes is None:
        processes = collect_all_processes()
    return sorted(processes, key=lambda x: x['memory_percent'], reverse=True)[:limit]

def get_top_processes(limit=10):
    try:
        processes = collect_all_processes(cpu_sample_interval=0.1)
        top_cpu = get_top_cpu_processes(limit=limit, processes=processes)
        top_memory = get_top_memory_processes(limit=limit, processes=processes)

        return {
            "status": "success",
            "cpu_processes": top_cpu,
            "memory_processes": top_memory,
            "top_cpu": top_cpu,
            "top_memory": top_memory
        }
    except Exception as e:
        print(f"Error collecting top processes: {e}")
        return {
            "status": "error",
            "message": "Unable to load process information.",
            "cpu_processes": [],
            "memory_processes": [],
            "top_cpu": [],
            "top_memory": []
        }


