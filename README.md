# Manga Reader

Paste a link, download the pages, read them offline.

**Download:** get `Manga Reader.exe` from the [Releases page](../../releases/latest). No install, no Python.

**Start:** double-click `Manga Reader.exe` or the Manga Reader shortcut on your desktop. It opens in its own window, and closing the window stops the app. Python isn't needed to run it.

## Where your manga is saved

The current folder is shown above your library. Click **Change** to pick a new one, and the app can move your existing manga there for you. **Open folder** shows it in File Explorer. Until you change it, manga is saved in `library/` next to the .exe.

Each manga gets its own folder, with its cover and a folder per chapter. Anything downloaded under the same title joins the same manga. The Title box suggests names already in your library, so you can add to one even if a site names it differently.

If you move `Manga Reader.exe`, the desktop shortcut needs recreating. The window is powered by Microsoft Edge, which comes with Windows 11, with its own separate profile. Your reading progress and chosen folder are stored in `%LOCALAPPDATA%\MangaReader`.

## Getting manga in

1. **Paste a link** to a series or a chapter. A series link lets you pick which chapters to download.
2. **Send pages from your browser**, for sites that turn the app away or build pages with scripts. With the app open, add the bookmark from the app to your normal browser's bookmarks bar, open the chapter, scroll to the end, click the bookmark. The confirmation opens in that browser, and the chapter lands in your library.
3. **Browse sources**: search GetComics, Comics-All, ComicsCodes (manga and comics lists) and JNovels from inside the app, or jump to Comic Book Plus. Open a result to see its downloads:
   - **Download** puts the file straight into your library (GetComics, PixelDrain and MediaFire links, and direct CBZ/CBR links).
   - **Read pages** loads an online reader page the same way as a pasted link.
   - **Open in browser** is for hosts that need a real browser (MEGA, ad link pages, captchas). Download the file there, then drop it on the library page.
4. **Add a folder** of your own collection. Every CBZ, ZIP, CBR and RAR in it and its subfolders is copied into your library: each subfolder becomes a series, each file a chapter or volume, and folders of loose images count too. Folders you add are remembered, so **Check for new files** picks up anything you've added since, skipping what's already in.
5. **Import files**: CBZ, CBR, ZIP, RAR or images, by button or drag and drop.

CBR and RAR files are opened with the `tar` that comes with Windows 10 and 11. A few newer RAR files need [7-Zip](https://www.7-zip.org/), which the app uses automatically when it's installed. PDFs aren't supported.

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

Edit `manga_reader.py`, `sources.py` (one parser per site, so a site changing its layout means changing one class) or `static/index.html`, then run `build_tools\build.bat` to rebuild the .exe (needs Python).

GitHub builds the .exe too: every push and pull request gets one under **Actions** > the run > **Artifacts**, and pushing a tag like `v1.1.0` publishes it on the Releases page.
