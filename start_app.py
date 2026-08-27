import subprocess
import sys
import os
import runpy
import auto_backup

def main():
    print("🚀 កំពុងចាប់ផ្តើមប្រព័ន្ធ NSP Cosmetic POS (ទម្រង់ ១-Tab ងាយស្រួលបិទ)...")
    
    # ០. ធ្វើការ Backup ទិន្នន័យមុនពេលប្រព័ន្ធដំណើរការ
    print("💾 កំពុងត្រួតពិនិត្យ និង Backup ទិន្នន័យ...")
    auto_backup.backup_database()
    
    work_dir = os.path.dirname(os.path.abspath(__file__))
    
    # ១. រត់ ABA Listener ស្ងាត់ៗនៅពីក្រោយ (កុំឱ្យវាមកដណ្តើម Tab)
    if getattr(sys, 'frozen', False):
        base_dir = sys._MEIPASS
    else:
        base_dir = work_dir

    aba_path = os.path.join(base_dir, "aba_listener.py")
    main_path = os.path.join(base_dir, "main.py")

    aba_process = subprocess.Popen([sys.executable, aba_path], cwd=base_dir)

    try:
        # ២. ទាញយក main.py មករត់ក្នុង Tab ដើមនេះផ្ទាល់តែម្តង!
        # ការធ្វើបែបនេះ ការពារមិនឱ្យ VS Code បើក Tab ថ្មីរញ៉េរញ៉ៃ
        sys.argv.append("--no-reload") # បិទ Reloader កុំឱ្យវិលវល់
        runpy.run_path(main_path, run_name="__main__")
        
    except KeyboardInterrupt:
        # ពេលបងចុច Ctrl + C វានឹងរត់ចូលកន្លែងនេះ
        pass
    finally:
        print("\n🛑 ទទួលបានបញ្ជា! កំពុងបោសសម្អាត និងបិទប្រព័ន្ធទាំងមូល...")
        aba_process.terminate()
        aba_process.wait()
        print("✅ ប្រព័ន្ធបានបិទរួចរាល់ ១០០% គ្មានសល់ខ្មោច Process ទេ!")

if __name__ == '__main__':
    main()