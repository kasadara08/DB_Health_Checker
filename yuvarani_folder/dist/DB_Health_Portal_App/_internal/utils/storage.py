import paramiko
 
 
hostname = "10.10.10.11"
username = "oracle"
password = "oracle123"
 
 
def get_remote_mounts():
 
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(
        paramiko.AutoAddPolicy()
    )
 
    ssh.connect(
        hostname,
        username=username,
        password=password
    )
 
    command = "df -hT"
 
    stdin, stdout, stderr = ssh.exec_command(command)
 
    output = stdout.read().decode()
 
    ssh.close()
 
    print(output)
 
 
get_remote_mounts()