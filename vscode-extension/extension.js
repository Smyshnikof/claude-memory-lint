// Claude Memory Lint — VS Code companion for claude-memory-lint (check.py).
// Activity Bar panel with lint findings, a badge with the issue count,
// editor diagnostics (Problems) and Ctrl+click on [[wikilinks]].

const vscode = require("vscode");
const cp = require("child_process");
const path = require("path");
const fs = require("fs");

// broken-link / broken-path / duplicate break memory navigation — errors;
// the rest degrades recall but links still resolve — warnings.
const ERRORS = new Set(["broken-link", "broken-path", "duplicate", "no-entry"]);
const MEMORY_RE = /[\\/]\.claude[\\/]projects[\\/][^\\/]+[\\/]memory[\\/]/i;
const WIKILINK = /\[\[([^\[\]]+)\]\]/g;

let treeView;
let diagnostics;

function config() {
  return vscode.workspace.getConfiguration("memoryLint");
}

// Resolution order: setting → canonical repo copy (dev mode) → bundled copy.
function findScript() {
  const custom = config().get("scriptPath");
  if (custom && fs.existsSync(custom)) return custom;
  const siblings = [
    path.join(__dirname, "..", "claude_memory_lint.py"), // public repo layout
    path.join(__dirname, "..", "memory-lint", "check.py"), // vendored layout
  ];
  for (const sibling of siblings) {
    if (fs.existsSync(sibling)) return sibling;
  }
  const bundled = path.join(__dirname, "check.py");
  return fs.existsSync(bundled) ? bundled : null;
}

function runLinter(args) {
  const script = findScript();
  if (!script) {
    return Promise.reject(new Error("check.py not found"));
  }
  const python = config().get("pythonPath") || "python";
  return new Promise((resolve, reject) => {
    cp.execFile(
      python,
      [script, ...args],
      { maxBuffer: 10 * 1024 * 1024 },
      (error, stdout, stderr) => {
        // exit 1 means "issues found"; stdout is still valid JSON
        if (error && !stdout) reject(new Error(stderr || error.message));
        else resolve(stdout);
      }
    );
  });
}

// --------------------------------------------------------------------------
// panel

function issueItem(memory, issue) {
  const file = path.join(memory, issue.note + ".md");
  const line = Math.max(0, (issue.line || 1) - 1);
  const item = new vscode.TreeItem(
    `${issue.note}.md` + (issue.line ? `:${issue.line}` : "")
  );
  item.description = issue.message;
  item.tooltip = `${issue.kind}: ${issue.message}`;
  item.iconPath = new vscode.ThemeIcon(
    ERRORS.has(issue.kind) ? "error" : "warning"
  );
  item.command = {
    command: "vscode.open",
    title: "Open note",
    arguments: [
      vscode.Uri.file(file),
      { selection: new vscode.Range(line, 0, line, 0) },
    ],
  };
  return item;
}

// One root row per memory folder (project); findings hang under their project.
function projectItem(result) {
  const norm = result.memory.replace(/\\/g, "/");
  const m = norm.match(/projects\/([^/]+)\/memory\/?$/i);
  const issues = result.issues;
  const item = new vscode.TreeItem(
    m ? m[1] : result.memory,
    issues.length
      ? vscode.TreeItemCollapsibleState.Expanded
      : vscode.TreeItemCollapsibleState.Collapsed
  );
  item.description = `${result.notes} notes · ${issues.length ? issues.length + " issues" : "clean"}`;
  item.tooltip = result.memory;
  item.iconPath = issues.length
    ? new vscode.ThemeIcon("warning", new vscode.ThemeColor("list.warningForeground"))
    : new vscode.ThemeIcon("check", new vscode.ThemeColor("testing.iconPassed"));
  const entry = path.join(result.memory, "MEMORY.md");
  if (fs.existsSync(entry)) {
    item.command = {
      command: "vscode.open",
      title: "Open MEMORY.md",
      arguments: [vscode.Uri.file(entry)],
    };
  }
  item.children = issues.length
    ? issues.map((issue) => issueItem(result.memory, issue))
    : [(() => {
        const ok = new vscode.TreeItem("no issues");
        ok.iconPath = new vscode.ThemeIcon("check");
        return ok;
      })()];
  return item;
}

class IssueTree {
  constructor() {
    this.emitter = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.emitter.event;
    this.roots = null; // null = never ran (viewsWelcome is shown)
  }

  getTreeItem(item) {
    return item;
  }

  getChildren(element) {
    return element ? element.children || [] : this.roots;
  }

  showResults(results) {
    this.roots = results.map(projectItem);
    this.emitter.fire();
    return results.reduce((n, r) => n + r.issues.length, 0);
  }

  showError(message) {
    const item = new vscode.TreeItem("Linter failed to run");
    item.description = message;
    item.tooltip = message;
    item.iconPath = new vscode.ThemeIcon("flame");
    this.roots = [item];
    this.emitter.fire();
  }
}

const tree = new IssueTree();

// --------------------------------------------------------------------------
// editor diagnostics (same findings as squiggles and in the Problems panel)

// Underline the offending fragment, not the whole line.
function rangeFor(lines, issue) {
  const lineNo = Math.max(0, (issue.line || 1) - 1);
  const text = lines[lineNo] || "";
  let start = 0;
  let end = text.length;
  const link = issue.message.match(/\[\[([^\[\]]+)\]\]/);
  if (link) {
    const idx = text.indexOf("[[" + link[1] + "]]");
    if (idx >= 0) {
      start = idx;
      end = idx + link[1].length + 4;
    }
  } else if (text.trim()) {
    start = text.length - text.trimStart().length;
    end = text.trimEnd().length;
  }
  return new vscode.Range(lineNo, start, lineNo, end);
}

function publishDiagnostics(results) {
  diagnostics.clear();
  for (const result of results) {
    const byFile = new Map();
    for (const issue of result.issues) {
      const file = path.join(result.memory, issue.note + ".md");
      if (!byFile.has(file)) byFile.set(file, []);
      byFile.get(file).push(issue);
    }
    for (const [file, issues] of byFile) {
      let lines = [];
      try {
        lines = fs.readFileSync(file, "utf8").split(/\r?\n/);
      } catch (_) {
        /* file is missing (no-entry) — diagnostic lands at 0:0 */
      }
      diagnostics.set(
        vscode.Uri.file(file),
        issues.map((issue) => {
          const d = new vscode.Diagnostic(
            rangeFor(lines, issue),
            issue.message,
            ERRORS.has(issue.kind)
              ? vscode.DiagnosticSeverity.Error
              : vscode.DiagnosticSeverity.Warning
          );
          d.source = "memory-lint";
          d.code = issue.kind;
          return d;
        })
      );
    }
  }
}

// --------------------------------------------------------------------------
// actions

async function lint() {
  try {
    // --all: lint every memory folder, however many projects there are
    const stdout = await runLinter(["--all", "--json"]);
    const results = JSON.parse(stdout);
    const total = tree.showResults(results);
    publishDiagnostics(results);
    treeView.badge = total
      ? { value: total, tooltip: `Claude memory: ${total} issues` }
      : undefined;
    treeView.description = `checked ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  } catch (err) {
    tree.showError(err.message);
    treeView.description = undefined;
  }
}

async function fix() {
  const pick = await vscode.window.showWarningMessage(
    "Run --fix? It rewrites unambiguous links and name: fields directly in the memory files.",
    { modal: true },
    "Fix"
  );
  if (pick !== "Fix") return;
  try {
    await runLinter(["--all", "--fix", "--json"]);
    await lint();
    vscode.window.showInformationMessage("Memory Lint: --fix applied.");
  } catch (err) {
    vscode.window.showErrorMessage("Memory Lint: " + err.message);
  }
}

// Ctrl+click on a [[wikilink]] opens the note it points to.
const linkProvider = {
  provideDocumentLinks(document) {
    const fsPath = document.uri.fsPath;
    if (!MEMORY_RE.test(fsPath)) return [];
    const dir = path.dirname(fsPath);
    const links = [];
    const text = document.getText();
    let m;
    WIKILINK.lastIndex = 0;
    while ((m = WIKILINK.exec(text)) !== null) {
      const target = path.join(dir, m[1].trim() + ".md");
      if (!fs.existsSync(target)) continue;
      const range = new vscode.Range(
        document.positionAt(m.index + 2),
        document.positionAt(m.index + 2 + m[1].length)
      );
      links.push(new vscode.DocumentLink(range, vscode.Uri.file(target)));
    }
    return links;
  },
};

// --------------------------------------------------------------------------

function activate(context) {
  diagnostics = vscode.languages.createDiagnosticCollection("memory-lint");
  treeView = vscode.window.createTreeView("memoryLint.issues", {
    treeDataProvider: tree,
  });

  context.subscriptions.push(
    diagnostics,
    treeView,
    vscode.commands.registerCommand("memoryLint.run", lint),
    vscode.commands.registerCommand("memoryLint.fix", fix),
    vscode.languages.registerDocumentLinkProvider(
      { language: "markdown" },
      linkProvider
    ),
    // saving a memory note re-lints automatically
    vscode.workspace.onDidSaveTextDocument((doc) => {
      if (MEMORY_RE.test(doc.uri.fsPath)) lint();
    })
  );

  // initial check (can be disabled via memoryLint.runOnStartup)
  if (config().get("runOnStartup")) lint();
}

function deactivate() {}

module.exports = { activate, deactivate };
