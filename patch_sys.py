import os

with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

if 'sys.path.append' not in content:
    prepend_str = '''import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
'''
    with open('dashboard/app.py', 'w', encoding='utf-8') as f:
        f.write(prepend_str + content.replace('import os\n', '', 1))

print("Fixed sys.path in app.py")
