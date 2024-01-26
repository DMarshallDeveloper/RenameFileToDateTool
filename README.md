This project is designed to rename files to the date they were made/ taken.
It uses exiftool to access the EXIF metadata about the files, mainly designed for images and videos. 
It gives the user the choice of renaming all the files in a selected folder to one of several attributes:
- Date Taken
- Date Modified
- Media Created
- Current filename (used to make all file naming consistent if files exist that have a different naming date structure e.g. '2018.03.03 12.53.24.jpg' to the format '2018-03-03 12.53.jpg'

- Current renaming format is to '%Y-%m-%d %H.%M'
