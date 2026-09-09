import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Download, FileUp, Upload } from "lucide-react";
import { vaultApi, vaultErrorMessage } from "./api";
import { Card } from "@/components/common/Card";
import { toast } from "@/stores/toast-store";
import type { VaultExportFormat, VaultImportFormat, VaultImportResult } from "./types";

const inputCls =
  "w-full px-3 py-2 text-sm rounded-md border border-border bg-elevated text-heading placeholder:text-muted focus:outline-none focus:border-accent transition-all";
const labelCls = "block text-xs font-medium text-muted mb-1";

const IMPORT_FORMATS: VaultImportFormat[] = [
  "bitwarden_json", "bitwarden_csv", "chrome_csv", "keepass_csv", "encrypted",
];
const EXPORT_FORMATS: VaultExportFormat[] = ["json", "csv", "encrypted"];

/** 导入导出：与 Bitwarden / Chrome / KeePass 互通 + 主密码加密备份。 */
export function PortablePanel() {
  const { t } = useTranslation("vault");
  const queryClient = useQueryClient();

  const [importFormat, setImportFormat] = useState<VaultImportFormat>("bitwarden_json");
  const [strategy, setStrategy] = useState<"skip" | "overwrite">("skip");
  const [importPassword, setImportPassword] = useState("");
  const [importResult, setImportResult] = useState<VaultImportResult | null>(null);
  const [importing, setImporting] = useState(false);

  const [exportFormat, setExportFormat] = useState<VaultExportFormat>("json");
  const [exportPassword, setExportPassword] = useState("");
  const [exporting, setExporting] = useState(false);

  const doImport = async (file: File) => {
    setImporting(true);
    setImportResult(null);
    try {
      const content = await file.text();
      const { data } = await vaultApi.importEntries({
        format: importFormat,
        content,
        strategy,
        master_password: importPassword,
      });
      setImportResult(data);
      queryClient.invalidateQueries({ queryKey: ["vaultEntries"] });
      queryClient.invalidateQueries({ queryKey: ["vaultTags"] });
      toast.success(t("portable.importDone"));
    } catch (err) {
      toast.error(vaultErrorMessage(err, t("messages.importFailed")));
    } finally {
      setImporting(false);
    }
  };

  const doExport = async () => {
    if (exportFormat !== "encrypted" &&
        !window.confirm(t("portable.confirmPlainExport"))) {
      return;
    }
    setExporting(true);
    try {
      const { data } = await vaultApi.exportEntries(exportFormat, exportPassword);
      const ext = exportFormat === "encrypted" ? "vault.enc.json" : exportFormat;
      const blob = new Blob([data.content], {
        type: exportFormat === "csv" ? "text/csv" : "application/json",
      });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `vault-export-${new Date().toISOString().slice(0, 10)}.${ext}`;
      link.click();
      URL.revokeObjectURL(link.href);
      toast.success(t("portable.exportDone"));
    } catch (err) {
      toast.error(vaultErrorMessage(err, t("messages.exportFailed")));
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Card title={t("portable.importTitle")} subtitle={t("portable.importSubtitle")}>
        <div className="space-y-3">
          <div>
            <label className={labelCls}>{t("portable.format")}</label>
            <select value={importFormat}
              onChange={(e) => setImportFormat(e.target.value as VaultImportFormat)}
              className={inputCls}>
              {IMPORT_FORMATS.map((f) => (
                <option key={f} value={f}>{t(`portable.formats.${f}`)}</option>
              ))}
            </select>
          </div>
          <div>
            <label className={labelCls}>{t("portable.strategy")}</label>
            <select value={strategy}
              onChange={(e) => setStrategy(e.target.value as "skip" | "overwrite")}
              className={inputCls}>
              <option value="skip">{t("portable.strategySkip")}</option>
              <option value="overwrite">{t("portable.strategyOverwrite")}</option>
            </select>
          </div>
          {importFormat === "encrypted" && (
            <div>
              <label className={labelCls}>{t("portable.backupPassword")}</label>
              <input type="password" value={importPassword}
                onChange={(e) => setImportPassword(e.target.value)}
                className={inputCls} />
            </div>
          )}
          <label
            className={`flex items-center justify-center gap-2 px-3 py-2.5 text-sm font-medium rounded-md border border-dashed border-border bg-elevated text-muted hover:border-accent hover:text-accent transition-all cursor-pointer ${importing ? "opacity-50 pointer-events-none" : ""}`}
          >
            <FileUp size={15} />
            {importing ? t("portable.importing") : t("portable.chooseFile")}
            <input
              type="file"
              className="hidden"
              accept=".json,.csv,.txt"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void doImport(file);
                e.target.value = "";
              }}
            />
          </label>
          {importResult && (
            <div className="text-xs text-muted space-y-1 p-3 rounded-md bg-hover">
              <div>{t("portable.resultAdded")}: {importResult.added}</div>
              <div>{t("portable.resultSkipped")}: {importResult.skipped}</div>
              <div>{t("portable.resultOverwritten")}: {importResult.overwritten}</div>
              {importResult.failed > 0 && (
                <div className="text-danger">
                  {t("portable.resultFailed")}: {importResult.failed}
                </div>
              )}
            </div>
          )}
        </div>
      </Card>

      <Card title={t("portable.exportTitle")} subtitle={t("portable.exportSubtitle")}>
        <div className="space-y-3">
          <div>
            <label className={labelCls}>{t("portable.format")}</label>
            <select value={exportFormat}
              onChange={(e) => setExportFormat(e.target.value as VaultExportFormat)}
              className={inputCls}>
              {EXPORT_FORMATS.map((f) => (
                <option key={f} value={f}>{t(`portable.exportFormats.${f}`)}</option>
              ))}
            </select>
          </div>
          {exportFormat === "encrypted" && (
            <div>
              <label className={labelCls}>{t("portable.backupPassword")}</label>
              <input type="password" value={exportPassword}
                onChange={(e) => setExportPassword(e.target.value)}
                className={inputCls} />
            </div>
          )}
          <p className="text-[11px] text-muted leading-relaxed">
            {t(`portable.exportHint.${exportFormat}`)}
          </p>
          <button
            onClick={doExport}
            disabled={exporting || (exportFormat === "encrypted" && !exportPassword)}
            className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-md bg-accent text-white hover:opacity-90 disabled:opacity-50 transition-all"
          >
            {exporting ? <Upload size={15} /> : <Download size={15} />}
            {t("portable.exportAction")}
          </button>
        </div>
      </Card>
    </div>
  );
}
