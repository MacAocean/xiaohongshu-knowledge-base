const { Modal, Notice, Plugin, SuggestModal, FileSystemAdapter, requestUrl } = require("obsidian");
const { spawn } = require("node:child_process");
const path = require("node:path");

class InputModal extends Modal {
  constructor(app, title, placeholder, submit) {
    super(app);
    this.title = title;
    this.placeholder = placeholder;
    this.submit = submit;
  }

  onOpen() {
    this.titleEl.setText(this.title);
    const form = this.contentEl.createEl("form");
    const input = form.createEl("textarea", { attr: {
      rows: "4", placeholder: this.placeholder, "aria-label": this.title
    }});
    input.style.width = "100%";
    form.createEl("button", { text: "执行", attr: { type: "submit" } });
    const submit = (event) => {
      event.preventDefault();
      const value = input.value.trim();
      if (!value) return;
      this.close();
      this.submit(value);
    };
    form.addEventListener("submit", submit);
    input.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") submit(event);
    });
    input.focus();
  }

  onClose() {
    this.contentEl.empty();
  }
}

class ResultModal extends SuggestModal {
  constructor(app, rows) {
    super(app);
    this.rows = rows;
    this.setPlaceholder("筛选或选择要打开的笔记");
  }

  getSuggestions(query) {
    return this.rows.filter((row) => `${row.title} ${row.excerpt || ""}`.includes(query.trim()));
  }

  renderSuggestion(row, element) {
    element.createEl("div", { text: row.title });
    if (row.excerpt) element.createEl("small", { text: row.excerpt });
  }

  onChooseSuggestion(row) {
    void this.app.workspace.openLinkText(row.path, "", false);
  }
}

module.exports = class VaultLocalTools extends Plugin {
  async onload() {
    const configuration = await this.loadData();
    this.python = configuration?.python || "/usr/bin/python3";
    this.children = new Set();
    this.running = new Set();
    this.registerObsidianProtocolHandler("kb-import-xhs", (params) => {
      this.app.workspace.onLayoutReady(() => void this.importSharedXhs(params.link));
    });
    this.addCommand({
      id: "transcribe-current",
      name: "转写当前视频",
      callback: () => this.current("视频转写.py", "run", "转写")
    });
    this.addCommand({
      id: "adopt-current-xhs",
      name: "将当前小红书导入笔记归入外部参考库",
      callback: () => this.current("小红书导入.py", "adopt", "归档")
    });
    this.addCommand({
      id: "xhs-metadata",
      name: "核验小红书作者与发布日期",
      callback: () => new InputModal(this.app, "核验小红书公开元数据", "每行粘贴一个已归档的原始分享链接", (value) => {
        void this.withNotice("核验元数据", () => this.verifyMetadata(value));
      }).open()
    });
    this.addCommand({
      id: "archive-current",
      name: "归档当前收件笔记",
      callback: () => this.current("知识库.py", "archive", "归档")
    });
    this.addCommand({
      id: "suggest-relations",
      name: "查看当前资料的关联候选",
      callback: async () => {
        const file = this.app.workspace.getActiveFile();
        if (!file || file.extension !== "md") return new Notice("先打开已归档的来源或知识笔记");
        const result = await this.withNotice("检索候选", () =>
          this.run("知识关系.py", ["suggest", file.path]));
        if (result?.candidates.length) new ResultModal(this.app, result.candidates).open();
        else if (result) new Notice("没有可供复核的候选");
      }
    });
    this.addCommand({
      id: "capture",
      name: "收录链接或文字",
      callback: () => new InputModal(this.app, "收录到知识库", "粘贴一个公开链接，或输入一段文字", (value) => {
        const kind = /^https?:\/\/\S+$/.test(value) ? "--url-stdin" : "--text";
        void this.withNotice("收录", () => this.run("知识库.py", ["collect", kind], value), true);
      }).open()
    });
    this.addCommand({
      id: "hybrid-search",
      name: "在个人知识库中混合检索",
      callback: () => new InputModal(this.app, "检索知识库", "输入要查找的问题", async (query) => {
        const rows = await this.withNotice("检索", () =>
          this.run("知识库.py", ["search", "--mode", "hybrid", "--limit", "12", "--", query]));
        if (rows?.length) new ResultModal(this.app, rows).open();
        else if (rows) new Notice("没有匹配的资料");
      }).open()
    });
  }

  async importSharedXhs(link) {
    const key = "shortcut:xiaohongshu";
    if (!link || this.running.has(key)) {
      new Notice(link ? "已有小红书笔记正在导入" : "没有收到小红书链接");
      return;
    }
    this.running.add(key);
    const notice = new Notice("正在处理小红书链接…", 0);
    try {
      const target = await this.run("小红书导入.py", ["shortcut-prepare"], link);
      if (target.existing) {
        await this.openResult(target.existing);
        new Notice("这条小红书笔记已收录");
        return;
      }
      const importer = this.app.plugins?.plugins?.["xiaohongshu-importer"];
      if (importer?.manifest?.version !== "1.1.3" || typeof importer.importXHSNote !== "function"
          || importer.settings?.defaultFolder !== "00_收件箱/_小红书原始导入") {
        throw new Error("标准版导入插件或暂存目录不匹配，请使用 Obsidian 中的标准导入命令");
      }
      const prefix = importer.settings.defaultFolder + "/";
      const paths = () => this.app.vault.getMarkdownFiles().map((file) => file.path)
        .filter((name) => name.startsWith(prefix));
      const prior = new Set(paths());
      await importer.importXHSNote(target.url, "外部创作参考", true);
      const available = paths();
      const added = available.filter((name) => !prior.has(name));
      const candidates = [...added, ...available.filter((name) => prior.has(name))];
      const result = await this.run("小红书导入.py", ["shortcut-finish", target.note_id], JSON.stringify(candidates));
      await this.openResult(result.path);
      new Notice(result.status === "ready" ? "小红书已归档到资料来源" : "小红书已登记，正文或媒体待补录", 8000);
    } catch (error) {
      new Notice(`小红书导入未完成。 ${error.message}`, 8000);
    } finally {
      notice.hide();
      this.running.delete(key);
    }
  }

  async verifyMetadata(text) {
    const rows = [];
    for (const input of text.split(/\s+/).filter(Boolean)) {
      let id = "未知";
      try {
        const url = new URL(input);
        const match = url.pathname.match(/^\/(?:explore|discovery\/item)\/([a-f0-9]{24})\/?$/);
        if (url.protocol !== "https:" || !["www.xiaohongshu.com", "xiaohongshu.com"].includes(url.hostname)
            || url.port || url.username || url.password || !match) throw new Error("链接格式不受支持");
        id = match[1];
        url.pathname = `/discovery/item/${id}`;
        const result = await requestUrl({ url: url.toString() });
        const raw = result.text.match(/window\.__INITIAL_STATE__\s*=\s*([\s\S]*?)<\/script>/)?.[1];
        if (!raw) throw new Error("公开页面未返回笔记详情");
        const state = JSON.parse(raw.trim().replace(/;$/, "").replace(/"(?:[^"\\]|\\.)*"|\bundefined\b/g,
          (match) => match === "undefined" ? "null" : match));
        const entry = state.note?.noteDetailMap?.[id]?.note;
        if (!entry || (entry.noteId && entry.noteId !== id)) throw new Error("未能核对目标笔记详情");
        rows.push(await this.run("小红书导入.py", ["metadata", id], JSON.stringify({
          note_id: id, author: entry.user?.nickname || entry.user?.nickName, published_ms: entry.time
        })));
      } catch {
        rows.push({ id, error: "公开页面没有返回可核对的字段，或该笔记尚未归档。" });
      }
    }
    const count = rows.filter((row) => !row.error).length;
    new Notice(`已核验 ${count}/${rows.length} 条元数据，成功结果见来源笔记属性。`, 9000);
    return { rows };
  }

  current(script, action, name) {
    const file = this.app.workspace.getActiveFile();
    if (!file || file.extension !== "md") {
      new Notice("先打开一篇 Markdown 笔记");
      return;
    }
    const key = `${action}:${file.path}`;
    if (this.running.has(key)) {
      new Notice("该笔记的操作正在执行");
      return;
    }
    this.running.add(key);
    void this.withNotice(name, () => this.run(script, [action, file.path]), action === "adopt")
      .finally(() => this.running.delete(key));
  }

  async withNotice(label, work, openResult = false) {
    const notice = new Notice(`${label}中…`, 0);
    try {
      const result = await work();
      notice.hide();
      new Notice(`${label}完成${result.segments ? ` · ${result.segments} 段` : ""}`);
      if (openResult && result.path) await this.openResult(result.path);
      return result;
    } catch (error) {
      notice.hide();
      new Notice(`${label}失败。 ${error.message}`, 8000);
      return null;
    }
  }

  async openResult(relative) {
    // The local child writes atomically; Obsidian's file watcher can learn the path a little later.
    for (let attempt = 0; attempt < 20; attempt++) {
      const file = this.app.vault.getFileByPath(relative);
      if (file) {
        await this.app.workspace.getLeaf(false).openFile(file);
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    new Notice("已保存。可从资料地图或快速切换打开新笔记。");
  }

  run(script, args, input = "") {
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) throw new Error("需要桌面端本地 Vault");
    const root = adapter.getBasePath();
    return new Promise((resolve, reject) => {
      const child = spawn(this.python, [path.join(root, "99_系统", "程序", script), ...args],
        { cwd: root, shell: false, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
      this.children.add(child);
      let stdout = "";
      let stderr = "";
      child.stdout.setEncoding("utf8");
      child.stderr.setEncoding("utf8");
      child.stdout.on("data", (data) => { stdout += data; });
      child.stderr.on("data", (data) => { stderr += data; });
      child.on("error", reject);
      child.stdin.on("error", reject);
      child.on("close", (code) => {
        this.children.delete(child);
        if (code !== 0) reject(new Error(stderr.trim().split("\n")[0] || `退出码 ${code}`));
        else {
          try { resolve(JSON.parse(stdout)); }
          catch { reject(new Error("本地工具没有返回可读取的结果")); }
        }
      });
      child.stdin.end(input, "utf8");
    });
  }

  onunload() {
    for (const child of this.children) child.kill("SIGTERM");
  }
};
