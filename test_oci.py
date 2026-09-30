import os
import sys
import oracledb

print("Python executable:", sys.executable)
print("Python version:", sys.version)

lib_dir = r"C:\Users\KTS\Desktop\dash_manually\oracle_client"
print(f"Checking for oci.dll in: {lib_dir}")
oci_path = os.path.join(lib_dir, "oci.dll")
print(f"oci.dll exists: {os.path.exists(oci_path)}")

if os.path.exists(oci_path):
    print("Files in client dir:", os.listdir(lib_dir))
    try:
        print("Attempting to initialize Oracle client in thick mode...")
        oracledb.init_oracle_client(lib_dir=lib_dir)
        print("SUCCESS! Oracle client initialized in thick mode successfully.")
    except Exception as e:
        print("FAILURE! Error during initialization:")
        import traceback
        traceback.print_exc()
else:
    print("oci.dll not found in the path. Please check the directory path.")
