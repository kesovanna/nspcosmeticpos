import sys, os

with open('start_app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'aba_process = subprocess.Popen' in line:
        lines[i] = """    if getattr(sys, 'frozen', False):
        base_dir = sys._MEIPASS
    else:
        base_dir = work_dir

    aba_path = os.path.join(base_dir, "aba_listener.py")
    main_path = os.path.join(base_dir, "main.py")

    aba_process = subprocess.Popen([sys.executable, aba_path], cwd=base_dir)\n"""
    elif 'runpy.run_path("main.py"' in line:
        lines[i] = '        runpy.run_path(main_path, run_name="__main__")\n'

with open('start_app.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
