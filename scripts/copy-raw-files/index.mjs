import { readdir, copyFile } from "fs/promises"
import path from "path"
import readline from "readline"
import { getRawDirs } from "./utils.mjs"

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
})

const end = () => rl.close()

const run = async () => {
  const rawDirs = await getRawDirs()

  rl.question("Paste the need-raw images ", async (raws) => {
    const needRaws = raws
      .split(/\.jpg\s?/gi)
      .map((l) => l.trim())
      .filter(Boolean)

    let imports = await Promise.all(
      needRaws.map(async (needRaw) => {
        const fileName = path.basename(needRaw)
        return {
          fileName,
          target: `${needRaw}.ARW`,
          // The first 4 characters of the folder created by the SONY camera are garbage
          source: (
            await Promise.all(
              rawDirs
                .filter(
                  (rawDir) => rawDir.name.slice(4) === fileName.slice(4, 8)
                )
                .map(async (dir) => {
                  const contents = await readdir(
                    path.join(dir.path, dir.name),
                    {
                      withFileTypes: true,
                    }
                  )
                  return contents.filter(
                    (c) =>
                      c.isFile() &&
                      c.name.slice(0, -4) === fileName.split("_")[1]
                  )
                })
            )
          ).flat(1),
        }
      })
    )

    const notFoundImports = imports.filter((i) => i.source.length === 0)
    if (notFoundImports.length) {
      console.error(
        `Error: ${notFoundImports.length} image(s) cannot be found in source SD card`
      )
      console.error(notFoundImports.map((n) => n.fileName))
      return end()
    }

    const multiPossibleImports = imports.filter((i) => i.source.length > 1)
    if (multiPossibleImports.length) {
      console.error(
        `Error: ${multiPossibleImports.length} image(s) have multiple source SD directories. This should only happen if you have more than a year of data. Update the code to handle this case or delete older directories.`
      )
      console.error(notFoundImports.map((n) => n.fileName))
      return end()
    }

    imports = imports.map((i) => {
      const source = i.source[0]
      return { ...i, source: path.join(source.path, source.name) }
    })

    for (const imp of imports) {
      console.log(`Will copy ${imp.source} to ${imp.target}`)
    }

    rl.question(`Import ${imports.length} images? (Y/n) `, async (y) => {
      if (y && y.toLocaleLowerCase() !== "y") {
        return end()
      }

      for (const imp of imports) {
        await copyFile(imp.source, imp.target)
        console.log(`Copied ${imp.source} to ${imp.target}`)
      }

      end()
    })
  })
}

run().catch(console.error)
