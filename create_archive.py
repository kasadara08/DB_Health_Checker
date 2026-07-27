import os
import subprocess
import zipfile

def generate_requirements():
    """Generates the requirements.txt file using the virtual environment's pip freeze."""
    project_root = os.path.dirname(os.path.abspath(__file__))
    req_file_path = os.path.join(project_root, "requirements.txt")
    
    # Look for pip in common venv location
    pip_path = os.path.join(project_root, "venv", "Scripts", "pip.exe")
    if not os.path.exists(pip_path):
        pip_path = os.path.join(project_root, ".venv", "Scripts", "pip.exe")
        
    try:
        if os.path.exists(pip_path):
            # Execute pip freeze from the virtual environment
            result = subprocess.run([pip_path, "freeze"], capture_output=True, text=True, check=True)
            dependencies = result.stdout
        else:
            # Fallback to current environment's pip freeze if venv is missing
            result = subprocess.run(["pip", "freeze"], capture_output=True, text=True, check=True)
            dependencies = result.stdout
            
        with open(req_file_path, "w", encoding="utf-8") as f:
            f.write(dependencies)
        print("requirements.txt successfully generated.")
    except Exception as e:
        print(f"Warning: Failed to generate requirements.txt using pip freeze ({e}). Keeping existing requirements.txt if present.")

def create_zip_archive():
    """Creates a ZIP archive containing all project files excluding system, virtual env, and cache folders."""
    project_root = os.path.dirname(os.path.abspath(__file__))
    zip_filename = "DB_dashboard.zip"
    zip_filepath = os.path.join(project_root, zip_filename)
    
    # Exclusion rules
    exclude_folders = {"__pycache__", ".git", "venv", ".venv", ".pytest_cache"}
    exclude_extensions = {".pyc"}
    
    files_added_count = 0
    
    try:
        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(project_root):
                # Filter out excluded directories in-place so os.walk doesn't traverse them
                dirs[:] = [d for d in dirs if d not in exclude_folders]
                
                for file in files:
                    # Exclude the generated ZIP file itself
                    if file == zip_filename:
                        continue
                        
                    file_ext = os.path.splitext(file)[1]
                    if file_ext in exclude_extensions:
                        continue
                        
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, project_root)
                    
                    zipf.write(full_path, rel_path)
                    files_added_count += 1
                    
        # Success output message
        print("\n" + "="*40)
        print(" ZIP ARCHIVE GENERATED SUCCESSFULLY")
        print("="*40)
        print(f"requirements.txt location : {os.path.join(project_root, 'requirements.txt')}")
        print(f"ZIP file location         : {zip_filepath}")
        print(f"Total files added         : {files_added_count}")
        print("="*40)
        
    except Exception as e:
        print(f"Error creating ZIP archive: {e}")

if __name__ == "__main__":
    generate_requirements()
    create_zip_archive()
