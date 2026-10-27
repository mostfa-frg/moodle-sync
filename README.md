# Moodle Material Sync

Downloads all your course files from the college Moodle portal into one folder per course.
Run it again whenever you want: **it only downloads what is missing.**

## Use it

1. Install Python 3.10+ (https://www.python.org/downloads/ - tick "Add Python to PATH" on Windows).
2. Get the tool: `git clone <this repo's address>` (or press the green **Code** button -> **Download ZIP**, then unzip).
3. Double-click **`Run Sync.bat`** (Windows) or run **`bash run.sh`** (Mac/Linux).
   - First run only: it installs what it needs. It uses the Chrome or Edge already on your computer;
     a browser is downloaded (~150 MB) only if you have neither.
4. A browser window opens. Log in to the portal, wait until you see your dashboard, then go back to the
   black window and press **Enter**.
5. Your files appear in **`Course Materials/<Course name>/`**.

Next time you run it, the login is remembered and only new files are downloaded.
To get tool updates later: run `git pull` (if you used the ZIP, just replace the old `moodle_sync.py` with the new one).

```
Course Materials/
  Distributed Systems/
    Lecture 1.pdf            <- single files
    Sections/Week 1/...      <- Moodle folders keep their own structure
    Assignments/assignment1.pdf   <- teacher's instruction files only
  Cloud Computing/                <- a course whose materials are links (e.g. Google Drive)
    Books/...                     <- downloaded from the Drive link named "Books"
    Unit 1/...
    URLs/                         <- a clickable shortcut for EVERY link in the course
      Books.url
      Links.md                    <- the same links as one readable list
```

## Courses whose materials are links (Google Drive)

Some teachers add their files as Moodle **"URL"** items that open Google Drive. The tool handles these:

- Every link is saved as a shortcut in the course's `URLs/` folder (double-click a `.url` file to open it;
  `Links.md` lists them all). This works for any website, not only Drive.
- If a link is a **Google Drive file or folder that is shared with "anyone with the link"**, the files are also
  downloaded into `<course>/<link name>/`. Re-runs only fetch files that are new.
- If the Drive link is **private** (limited to certain accounts), it can't be downloaded. The tool says so, and
  you open the shortcut and download it yourself. Google can also temporarily block very popular files
  ("too many downloads"); just run again later.
- Big Drive folders (books!) can be large, so the first run may take a while. Use `--no-drive` to only save
  the shortcuts and skip downloading.
- Google Docs / Slides / Sheets links are saved as shortcuts only.

## Options

```
python moodle_sync.py --login               # log in again (e.g. wrong account / expired)
python moodle_sync.py --course 1326 1325    # only these courses (ID = number at the end of the course page address)
python moodle_sync.py --out "D:\University"   # save somewhere else
python moodle_sync.py --no-drive            # only save link shortcuts, don't download Google Drive files
python moodle_sync.py --force               # re-download everything, overwriting
```

## Good to know

- **Already-downloaded files are safe.** If a file with the same name is already in the course folder
  (even one you downloaded by hand), it is kept, not downloaded again. Delete a file and the next run restores it.
- If your course list can't be detected automatically, use `--course` with the IDs.
- If a teacher *replaces* an existing file with a new version under the same name, it is not re-downloaded
  (use `--force`, or delete the old file).
- Downloaded: files, folders, assignment instruction attachments and public Google Drive links. Not
  downloaded: other websites' content (saved as shortcuts only), Moodle "pages", quizzes, forums, and your
  own submitted work.
- It only reads from the portal. It never submits or changes anything.
- The portal currently shows a certificate warning, so the tool ignores HTTPS certificate errors (same as the
  original project). Only use it on a network you trust.
- **`.session/portal-state.json` is your login. Never share it or send it to anyone.**
  The included `.gitignore` keeps it (and your downloaded files) out of Git, so don't remove those lines.
