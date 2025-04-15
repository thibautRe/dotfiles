import { readdir } from "fs/promises"

/** Returns the raw directories at the DCIM folder */
export const getRawDirs = async (rawDir = `/run/media/thibaut/disk/DCIM/`) => {
  try {
    return (await readdir(rawDir, { withFileTypes: true })).filter((e) =>
      e.isDirectory()
    )
  } catch (err) {
    if (err.code === "ENOENT") {
      console.error(
        `[Error] Cannot find directory ${rawDir}. Is the SD card mounted?`
      )
      return process.exit(1)
    }
    throw err
  }
}
