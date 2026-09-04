import { useEffect, useMemo, useState } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import {
  AlertCircle,
  Braces,
  Copy,
  Eye,
  FileText,
  LoaderCircle,
  Pencil,
  Play,
  Save,
  X
} from "lucide-react";
import type { FileItem, LocalDocument } from "../../shared/types";
import { formatJsonText } from "../../shared/jsonFormat";
import { getFormatFromExtension } from "../../shared/fileTypes";
import { formatBytes, formatLabel, toDocStreamUrl } from "../utils";
import { useToast } from "./Toast";
import { ConfirmDialog } from "./ConfirmDialog";

export type ViewerMode = "preview" | "edit";

export interface DocumentViewerRequest {
  file: FileItem;
  mode: ViewerMode;
}

interface DocumentViewerProps {
  request: DocumentViewerRequest;
  onClose: () => void;
  /** 另存为成功后通知父级更新文件行的路径信息；byteLength 用于刷新文件大小展示。 */
  onPathChanged: (fileId: string, path: string, byteLength?: number) => void;
}

const TEXT_MONO_HINT = "提示：编辑模式下可直接修改文本，Ctrl+S 保存。";

function splitPath(filePath: string): { dir: string; name: string } {
  const normalized = filePath.replace(/\\/g, "/");
  const parts = normalized.split("/");
  return { dir: parts.slice(0, -1).join("/") || normalized, name: parts.at(-1) ?? filePath };
}

export function DocumentViewer({ request, onClose, onPathChanged }: DocumentViewerProps) {
  const toast = useToast();
  const { file } = request;

  const [doc, setDoc] = useState<LocalDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [lastSaved, setLastSaved] = useState<string | null>(null);
  const [mode, setMode] = useState<ViewerMode>(request.mode);
  const [saving, setSaving] = useState(false);
  const [askClose, setAskClose] = useState(false);

  const dirty = lastSaved !== null && content !== lastSaved;

  const reload = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const result = await window.formatFlow.readDoc(file.path);
      if (!result.ok) {
        setLoadError(result.error);
        return;
      }
      setDoc(result.doc);
      setContent(result.doc.content ?? "");
      setLastSaved(result.doc.content ?? null);
      setMode(result.doc.kind === "text" ? request.mode : "preview");
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "读取文件失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
    // 仅在打开的文件变化时重新加载。
  }, [file.path]);

  const isText = doc?.kind === "text";
  const isJson = doc?.format === "json";
  const isMedia = doc?.kind === "media";

  const markdownHtml = useMemo(() => {
    if (!isText || doc?.format !== "markdown") return "";
    try {
      return DOMPurify.sanitize(marked.parse(content) as string);
    } catch {
      return DOMPurify.sanitize(String(content));
    }
  }, [isText, doc?.format, content]);

  const jsonPreview = useMemo(() => {
    if (!isJson) return null;
    return formatJsonText(content);
  }, [isJson, content]);

  const handleSave = async (): Promise<boolean> => {
    if (!doc || !isText || !dirty) return false;
    setSaving(true);
    try {
      const result = await window.formatFlow.saveDoc(doc.path, content);
      if (!result.ok) {
        toast.error(result.error ?? "保存失败");
        return false;
      }
      setLastSaved(content);
      toast.success(`已保存：${doc.name}`);
      return true;
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAs = async (): Promise<void> => {
    if (!doc) return;
    setSaving(true);
    try {
      const result = await window.formatFlow.saveDocAs(doc.path, doc.name, isText ? content : null);
      if (result.ok && result.path) {
        const newPath = result.path;
        const { name } = splitPath(newPath);
        const extension = name.includes(".") ? name.slice(name.lastIndexOf(".") + 1).toLowerCase() : "";
        const format = getFormatFromExtension(extension);
        const byteLength = isText ? new TextEncoder().encode(content).length : undefined;
        setDoc({ ...doc, path: newPath, name, extension, format, category: doc.category });
        if (isText) setLastSaved(content);
        onPathChanged(file.id, newPath, byteLength);
        toast.success(`已另存为：${name}`);
      } else if (!result.ok && !result.canceled) {
        toast.error(result.error ?? "另存为失败");
      }
    } finally {
      setSaving(false);
    }
  };

  const handleFormatJson = (): void => {
    if (!isJson) return;
    const result = formatJsonText(content);
    if (result.ok) {
      if (result.text !== content) {
        setContent(result.text);
        toast.info("JSON 已美化，保存后生效");
      }
    } else {
      toast.error(result.error);
    }
  };

  const requestClose = () => {
    if (dirty) {
      setAskClose(true);
    } else {
      onClose();
    }
  };

  const closeDiscarding = () => {
    setAskClose(false);
    onClose();
  };

  const saveAndClose = async () => {
    const saved = await handleSave();
    if (saved) {
      setAskClose(false);
      onClose();
    }
  };

  const docIcon = isMedia ? <Play size={16} /> : isJson ? <Braces size={16} /> : <FileText size={16} />;
  const previewNote = isText
    ? dirty
      ? "预览为只读模式，当前存在未保存的修改"
      : "预览为只读模式，如需修改请切换到编辑"
    : "二进制媒体文件仅支持预览与另存为";

  return (
    <div className="viewer-backdrop" role="presentation" onMouseDown={requestClose}>
      <section
        className="viewer"
        role="dialog"
        aria-modal="true"
        aria-label={`文档查看器：${file.name}`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="viewer__header">
          <div className="viewer__title">
            {docIcon}
            <h2>
              {doc?.name ?? file.name}
              {dirty ? <span className="viewer__dirty">*</span> : null}
            </h2>
            {doc ? (
              <span className={`format-badge format-badge--${doc.format}`}>{formatLabel(doc.format)}</span>
            ) : null}
          </div>

          <div className="viewer__actions">
            {isText ? (
              <div className="viewer__segmented" role="tablist" aria-label="查看模式">
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === "preview"}
                  className={mode === "preview" ? "is-active" : ""}
                  onClick={() => setMode("preview")}
                >
                  <Eye size={14} />
                  预览
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === "edit"}
                  className={mode === "edit" ? "is-active" : ""}
                  onClick={() => setMode("edit")}
                >
                  <Pencil size={14} />
                  编辑
                </button>
              </div>
            ) : null}

            {isJson ? (
              <button type="button" className="viewer__tool" onClick={handleFormatJson} title="美化 JSON（缩进格式化）">
                <Braces size={15} />
                美化
              </button>
            ) : null}

            <button
              type="button"
              className="viewer__tool viewer__tool--primary"
              disabled={!dirty || saving}
              onClick={() => void handleSave()}
              title={!isText ? "二进制媒体不支持修改" : dirty ? "保存修改 (Ctrl+S)" : "内容未修改"}
            >
              {saving ? <LoaderCircle size={15} className="spin" /> : <Save size={15} />}
              保存
            </button>
            <button
              type="button"
              className="viewer__tool"
              disabled={saving}
              onClick={() => void handleSaveAs()}
              title="将当前内容另存到其它位置"
            >
              <Copy size={15} />
              另存为
            </button>
            <button
              type="button"
              className="viewer__tool viewer__close"
              onClick={requestClose}
              aria-label="关闭"
              title={dirty ? "关闭（存在未保存修改）" : "关闭"}
            >
              <X size={16} />
            </button>
          </div>
        </header>

        <div className="viewer__meta">
          <span title={doc?.path ?? file.path}>{doc?.path ?? file.path}</span>
          {doc ? <span>{formatBytes(doc.sizeBytes)}</span> : null}
          {doc?.encoding ? <span>编码：{doc.encoding}</span> : null}
          {doc ? <span>{doc.kind === "text" ? "文本" : "媒体"}</span> : null}
        </div>

        <div className={`viewer__body viewer__body--${isMedia ? "media" : "text"}`}>
          {loading ? (
            <div className="viewer__status">
              <LoaderCircle size={22} className="spin" />
              <span>正在读取文件…</span>
            </div>
          ) : loadError ? (
            <div className="viewer__status viewer__status--error">
              <AlertCircle size={24} />
              <strong>无法打开文件</strong>
              <span>{loadError}</span>
              <div className="viewer__status-actions">
                <button type="button" className="primary-button" onClick={() => void reload()}>
                  重试
                </button>
                <button type="button" onClick={onClose}>
                  关闭
                </button>
              </div>
            </div>
          ) : isMedia ? (
            <div className="viewer__media">
              <video controls preload="metadata" src={toDocStreamUrl(file.path)}>
                当前环境不支持播放该视频。
              </video>
              <p className="viewer__notice">
                <AlertCircle size={14} />
                {previewNote}。可点击"另存为"复制到其它位置。
              </p>
            </div>
          ) : mode === "edit" ? (
            <div className="viewer__editor">
              <textarea
                aria-label={`编辑 ${doc?.name ?? ""}`}
                value={content}
                spellCheck={false}
                onChange={(event) => setContent(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
                    event.preventDefault();
                    void handleSave();
                  }
                }}
              />
              <p className="viewer__hint">{TEXT_MONO_HINT}</p>
            </div>
          ) : doc?.format === "markdown" ? (
            <article
              className="markdown-body"
              dangerouslySetInnerHTML={{ __html: markdownHtml || "<p>（空文档）</p>" }}
            />
          ) : isJson ? (
            <div className="viewer__plain">
              {jsonPreview?.ok ? (
                <pre>{jsonPreview.text || "（空对象）"}</pre>
              ) : (
                <>
                  <p className="viewer__notice viewer__notice--warn">
                    <AlertCircle size={14} />
                    JSON 格式无效：{jsonPreview?.error}
                  </p>
                  <pre>{content}</pre>
                </>
              )}
            </div>
          ) : (
            <div className="viewer__plain">
              <pre>{content || "（空文档）"}</pre>
            </div>
          )}
        </div>

        {isText ? (
          <footer className="viewer__footer">
            <span className={dirty ? "viewer__footer--dirty" : ""}>
              {dirty ? "● 未保存的修改" : "已保存 / 无修改"}
            </span>
            <span>{previewNote}</span>
          </footer>
        ) : null}
      </section>

      {askClose ? (
        <ConfirmDialog
          title="存在未保存的修改"
          message={`「${doc?.name ?? file.name}」的修改尚未保存，关闭将丢失这些修改。`}
          confirmLabel="保存并关闭"
          extraLabel="不保存"
          onConfirm={() => void saveAndClose()}
          onExtra={closeDiscarding}
          onCancel={() => setAskClose(false)}
        />
      ) : null}
    </div>
  );
}
