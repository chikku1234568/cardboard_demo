import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Store } from "../core/store.js";
import { FLAGSHIP } from "../core/paths.js";

/** A fresh writable copy of a project store in a temp workspace. */
export function freshStore(pid = FLAGSHIP): Store {
  const base = mkdtempSync(join(tmpdir(), "vcs-test-"));
  return Store.openWorkspace(pid, { base });
}
