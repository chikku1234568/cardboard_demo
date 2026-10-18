import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const VCS_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const REPO_ROOT = resolve(VCS_ROOT, "..");
export const DATA = join(REPO_ROOT, "data");
export const DATA_PROJECTS = join(DATA, "vcs", "projects");
export const MEDIA = join(DATA, "media");
export const WORKSPACE = process.env.VCS_WORKSPACE ?? join(VCS_ROOT, ".workspace");
export const RENDERS = join(VCS_ROOT, "renders");
export const FLAGSHIP = "proj_launch_week_recap";
