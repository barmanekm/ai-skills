# Linking a consumer to a local provider worktree

Paths are relative to the task directory (`tasks/<ID>/`), e.g. provider `lib`, consumer `api`.
Prefer mechanisms that live outside committed files; otherwise revert before committing.

| Ecosystem | Link (temporary) | Notes / revert |
|---|---|---|
| npm | `cd api && npm install ../lib` (or `npm link ../lib`) | changes package.json/lock: revert with `git -C api checkout -- package.json package-lock.json` |
| pnpm / yarn | `pnpm link ../lib` / `yarn link ../lib` | same caveat for manifests/lockfiles |
| TypeScript monorepo-style | `paths` mapping in a local, untracked `tsconfig.local.json` | keep untracked |
| Maven | `mvn -q install -DskipTests` in `lib`, then build `api` against the SNAPSHOT version | installs to `~/.m2`; bump consumer version only if needed |
| Gradle | `./gradlew build --include-build ../lib` (composite build) | nothing to revert |
| .NET | temporary `<ProjectReference Include="../../lib/src/Lib.csproj" />` or local NuGet feed via `dotnet pack -o ../localfeed` | revert csproj edits |
| Go | `go work init . ../lib` (go.work) | keep `go.work` uncommitted; avoid `replace` in go.mod |
| Python | `pip install -e ../lib` in the consumer's venv | nothing committed |
| Cargo | `[patch."<registry-or-git>"] lib = { path = "../lib" }` in `.cargo/config.toml` (untracked) or Cargo.toml | revert if in Cargo.toml |
| Generated clients (OpenAPI/proto) | regenerate from the provider's local spec file | commit the regenerated code only if the repo normally commits it |

Before committing, run `tw diff` and check no link artefacts remain.
