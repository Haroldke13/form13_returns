# Tkinter Form 14 Desktop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows-friendly Tkinter desktop version of the Flask Form 14 workflow under `TKINTER_FORM_14`.

**Architecture:** Keep the desktop app self-contained with a standard-library SQLite repository, a workflow service that mirrors the Flask create/list/detail/edit/analysis/export routes, and a Tkinter UI that exposes the same form sections and option lists. The database layer creates protection triggers and backups instead of any destructive delete path.

**Tech Stack:** Python 3, Tkinter/ttk, sqlite3, csv, unittest.

---

### Task 1: Desktop Data Workflow

**Files:**
- Create: `TKINTER_FORM_14/tests/test_desktop_workflow.py`
- Create: `TKINTER_FORM_14/form14_tkinter/schema.py`
- Create: `TKINTER_FORM_14/form14_tkinter/options.py`
- Create: `TKINTER_FORM_14/form14_tkinter/database.py`
- Create: `TKINTER_FORM_14/form14_tkinter/workflow.py`

- [ ] **Step 1: Write failing tests** covering report save/update, register summaries, CSV exports, analytics summaries, and delete triggers.
- [ ] **Step 2: Run** `python -m unittest discover -s TKINTER_FORM_14/tests -v` and confirm import failures.
- [ ] **Step 3: Implement** the schema, repository, option lists, parsing helpers, and workflow service.
- [ ] **Step 4: Run** `python -m unittest discover -s TKINTER_FORM_14/tests -v` and confirm all tests pass.

### Task 2: Tkinter UI

**Files:**
- Create: `TKINTER_FORM_14/form14_tkinter/app.py`
- Create: `TKINTER_FORM_14/run_form14_tkinter.py`

- [ ] **Step 1: Build** a notebook UI with New/Edit Form, Reports, Detail, Visualizations, and Database Tools tabs.
- [ ] **Step 2: Wire** add-row controls for repeated Form 14 sections and save/update actions through the workflow service.
- [ ] **Step 3: Verify** the app module imports without starting a Tk root during tests.

### Task 3: Windows Run Notes

**Files:**
- Create: `TKINTER_FORM_14/README.md`
- Create: `TKINTER_FORM_14/requirements.txt`

- [ ] **Step 1: Document** Windows launch commands, default database location, backup behavior, and delete-protection limits.
- [ ] **Step 2: Run** syntax compilation and unit tests.
