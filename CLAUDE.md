# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MakoletDashboard is a dashboard project. The codebase is in early development stages.

## Git & GitHub Workflow

**IMPORTANT**: As you work on this project, commit and push changes to GitHub regularly. This ensures we never lose work status and have a complete history of development.

- **Repository**: https://github.com/RoeiAmsalem/MakoletDashboard
- **Main branch**: `main` (production-ready code)
- **Commit frequency**: Make commits after completing logical units of work (features, bug fixes, refactoring). Don't wait until the end of the session.
- **Commit messages**: Use clean, descriptive messages following conventional commits format:
  - `feat:` for new features
  - `fix:` for bug fixes
  - `refactor:` for code refactoring
  - `docs:` for documentation
  - `test:` for test additions/changes
  - Example: `feat: Add user authentication` or `fix: Resolve dashboard loading bug`
- **Push to GitHub**: Push commits to GitHub after each logical completion or at natural breakpoints in work
- **User info**: Roei Amsalem (roei_amsalem@example.com)

**Workflow**: As work is completed → commit with clear message → push to GitHub → repeat

## Development Setup

As the project develops, add the following sections with actual commands:

- [ ] Installation/setup commands
- [ ] How to run the development server
- [ ] How to build for production
- [ ] How to run tests (unit, integration, e2e)
- [ ] How to run linting and formatting
- [ ] Any required environment variables or configuration

## Architecture & Structure

As the project develops, document:

- High-level architecture and design decisions
- Directory structure and organization
- Key modules and their responsibilities
- Data flow between major components
- External service integrations (if any)

## Context Window Statusline

Two scripts live in `~/.claude/` to monitor the Claude Code context window:

- `ctxstats` — prints a one-line snapshot: `MakoletDashboard | ctx: 21% used  (38k / 180k tokens)`
- `ctxwatch` — live-updating display with a progress bar, refreshes every 3s

**Usage**: Open a VS Code split terminal (drag the terminal tab or use the split icon), then run:
```bash
ctxwatch
```

Both aliases are defined in `~/.zshrc`.

## Important Notes for Future Development

- Update this file as the architecture and structure becomes clearer
- Keep architecture documentation focused on "big picture" patterns
- Document any complex algorithms or non-obvious design choices
- Link to important configuration files or documentation
