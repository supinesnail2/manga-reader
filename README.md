# Manga Reader

Paste a link, download the pages, read them offline.

**Start:** double-click `Manga Reader.exe` or the Manga Reader shortcut on your desktop. It opens in its own window, and closing the window stops the app. Python isn't needed to run it.

## Where your manga is saved

The current folder is shown above your library. Click **Change** to pick a new one, and the app can move your existing manga there for you. **Open folder** shows it in File Explorer. Until you change it, manga is saved in `library/` next to the .exe.

Each manga gets its own folder, with its cover and a folder per chapter. Anything downloaded under the same title joins the same manga. The Title box suggests names already in your library, so you can add to one even if a site names it differently.

If you move `Manga Reader.exe`, the desktop shortcut needs recreating. The window is powered by Microsoft Edge, which comes with Windows 11, with its own separate profile. Your reading progress and chosen folder are stored in `%LOCALAPPDATA%\MangaReader`.

## Getting manga in

1. **Paste a link** to a series or a chapter. A series link lets you pick which chapters to download.
2. **Send pages from your browser**, for sites that turn the app away or build pages with scripts. With the app open, add the bookmark from the app to your normal browser's bookmarks bar, open the chapter, scroll to the end, click the bookmark. The confirmation opens in that browser, and the chapter lands in your library.
3. **Import files**: CBZ, ZIP or images, by button or drag and drop.

## Reading

| Keys | Action |
| --- | --- |
| Arrow keys, A / D, or click the page sides | Previous / next page |
| Space | Scroll down, then next page |
| `[` `]` | Previous / next chapter |
| `+` `-` `0`, Ctrl + wheel | Zoom in, out, reset |
| Drag | Move around a zoomed page |
| H / W / O | Fit page, fit width, actual size |
| M | Single, double, scroll |
| S | Shift which pages sit together in double mode |
| G | Space between pages in scroll mode (turn off for webtoons) |
| R | Right-to-left |
| T | Page thumbnails |
| F | Fullscreen |
| Tab, or click the middle of the page | Hide the bars |
| Esc | Back to the chapter list |

## Changing the app

Edit `manga_reader.py` or `static/index.html`, then run `build_tools\build.bat` to rebuild the .exe (needs Python).
