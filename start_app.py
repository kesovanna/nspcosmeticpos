import subprocess
import sys
import time
import os

def main():
    print("🚀 កំពុងចាប់ផ្តើមប្រព័ន្ធ NSP Cosmetic POS និង ABA Listener...")
    
    # កំណត់ផ្លូវទៅកាន់ថតដែលផ្ទុកកូដពិតប្រាកដ
    work_dir = os.path.dirname(os.path.abspath(__file__))
    
    # បញ្ជាឱ្យបើក File ដោយប្រើ cwd (Current Working Directory)
    # នេះនឹងធ្វើឱ្យ Python យល់ថាវាស្ថិតនៅក្នុងថតកូដរបស់អ្នក
    pos_process = subprocess.Popen([sys.executable, "main.py"], cwd=work_dir)
    aba_process = subprocess.Popen([sys.executable, "aba_listener.py"], cwd=work_dir)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 កំពុងបិទប្រព័ន្ធទាំងពីរ...")
        pos_process.terminate()
        aba_process.terminate()
        pos_process.wait()
        aba_process.wait()
        print("✅ ប្រព័ន្ធបានបិទដោយជោគជ័យ!")

if __name__ == '__main__':
    main()