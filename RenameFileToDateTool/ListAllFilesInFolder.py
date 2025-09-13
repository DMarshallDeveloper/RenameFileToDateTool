import os
from tkinter import Tk, filedialog

def select_folder():
    root = Tk()
    root.withdraw()  # Hide the main window
    folder_selected = filedialog.askdirectory(title="Select a folder")
    return folder_selected

def list_all_file_names(directory):
    for root, dirs, files in os.walk(directory):
        for file in files:
            print(file)

def main():
    folder_path = select_folder()
    if folder_path:
        list_all_file_names(folder_path)
    else:
        print("No folder was selected.")

if __name__ == "__main__":
    main()
