---
name: cross-repo-change
description: Plan and implement a code change that spans several repositories of the current task so they stay in sync - shared libraries, API contracts, schemas, generated clients, config keys. Use when a change in one repo requires matching changes in another, or when asked to propagate, synchronise or apply a change across repos.
---

# Synchronised change across repositories

Works inside the active task (`tasks/<ID>/`). Repos in scope are in `task.yaml`; git lifecycle is handled by the task-workspace skill.

## 1. Map the dependency
- Find how the task's repos depend on each other: package manifests, API specs (OpenAPI, proto, GraphQL), shared schemas, generated code, env/config keys.
- Classify each repo as **provider** (defines the contract) or **consumer** (uses it). Show the order in one line, e.g. `lib → api → web`.
- A repo that must change but is not in the task → ask the user to add it (`tw add <ID> <repo>`). Never edit outside the task.

## 2. Providers first
- Make the provider change. Prefer backward-compatible steps (add before remove, optional before required) so consumers can move independently.
- Build and run its tests.

## 3. Link consumers to the local provider
- Point consumers at the provider's worktree instead of a published version. Ecosystem specifics: `references/local-linking.md`.
- Linking changes are temporary: keep them out of commits, or revert them before committing. Tell the user they exist.

## 4. Update consumers
- Search every consumer for all usages of the changed symbols, endpoints, fields or keys, and update them. Search, don't guess.
- Build and test each consumer against the local provider.

## 5. Verify together
- Run the tests of every repo involved. Report per repo: files changed, test result.

## 6. Hand off
- Remove/revert local links, then commit via the task-workspace skill with the same message across repos.
- PR descriptions state the merge order and which provider version consumers must bump to once it is released.
