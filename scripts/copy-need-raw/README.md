# copy-need-raw

This script is used to copy RAWs from my SD card to original source locations.

My workflow is: I tag pictures in Digikam with a specific tag ("need-raw"), I then select all pictures in this tag and CTRL+C them and use this script to import similarly named RAW files from the SD card to the original location of the copied files. Afterwards, I update the pictures tagged "need-raw" with "has-raw" instead.

## Usage

```sh
node index.mjs
# or alternatively, use the alias provided in zshrc:
copy-need-raw
```

Paste the image list after running the script. You need to paste it as one line (VSCode terminal or kitty works)
