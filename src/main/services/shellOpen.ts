import { shell } from "electron";

export async function openFolderInShell(folderPath: string): Promise<string> {
  return shell.openPath(folderPath);
}
