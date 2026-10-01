/** 身份管理面板共享表单模态 — 声纹说话人 / 人脸人物两域的同构表单。
 *
 * confirm（临时身份转正）/ merge（身份合并）/ enroll（媒体注册）三个表单
 * 在两域面板中逐块同构，收敛于此；所有用户可见文案经 props 注入
 * （i18n 键留在各面板命名空间），表单状态由模态自管理（目标切换时复位）。
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Input, Modal, Select } from "@/components/ui";

// ------------------------------------------------------------------
// confirm：临时身份 → 正式（命名 + 角色）
// ------------------------------------------------------------------

export interface ConfirmIdentityModalLabels {
  title: string;
  namePlaceholder: string;
  rolePlaceholder: string;
  confirmLabel: string;
}

export function ConfirmIdentityModal({
  open, onClose, onSubmit, pending, labels,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (name: string, role: string) => void;
  pending: boolean;
  labels: ConfirmIdentityModalLabels;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  useEffect(() => {
    if (open) {
      setName("");
      setRole("");
    }
  }, [open]);
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={labels.title}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>{t("common:cancel")}</Button>
          <Button loading={pending} disabled={!name.trim()} onClick={() => onSubmit(name.trim(), role.trim())}>
            {labels.confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Input placeholder={labels.namePlaceholder} value={name}
          onChange={(e) => setName(e.target.value)} />
        <Input placeholder={labels.rolePlaceholder} value={role}
          onChange={(e) => setRole(e.target.value)} />
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------
// merge：源身份并入目标身份（候选下拉）
// ------------------------------------------------------------------

export interface MergeIdentityModalLabels {
  title: string;
  hint: string;
  targetPlaceholder: string;
  mergeLabel: string;
}

export function MergeIdentityModal<T extends { id: number }>({
  source, candidates, getOptionLabel, onClose, onSubmit, pending, labels,
}: {
  source: T | null;
  candidates: T[];
  getOptionLabel: (item: T) => string;
  onClose: () => void;
  onSubmit: (targetId: number) => void;
  pending: boolean;
  labels: MergeIdentityModalLabels;
}) {
  const { t } = useTranslation();
  const [targetId, setTargetId] = useState("");
  useEffect(() => {
    if (source) setTargetId("");
  }, [source]);
  return (
    <Modal
      open={source !== null}
      onClose={onClose}
      title={labels.title}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>{t("common:cancel")}</Button>
          <Button variant="danger" loading={pending} disabled={!targetId}
            onClick={() => onSubmit(Number(targetId))}>
            {labels.mergeLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm">
        <p className="text-muted">{labels.hint}</p>
        <Select className="w-full" value={targetId} onChange={(e) => setTargetId(e.target.value)}>
          <option value="">{labels.targetPlaceholder}</option>
          {candidates.map((item) => (
            <option key={item.id} value={item.id}>{getOptionLabel(item)}</option>
          ))}
        </Select>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------
// enroll：媒体文件注册（音频/图片），可选实体 scope 字段
// ------------------------------------------------------------------

export interface EnrollIdentityModalLabels {
  title: string;
  namePlaceholder: string;
  rolePlaceholder: string;
  scopePlaceholder?: string;
  hint: string;
  enrollLabel: string;
}

export function EnrollIdentityModal({
  open, onClose, onSubmit, pending, labels, accept, scope,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (name: string, role: string, file: File) => void;
  pending: boolean;
  labels: EnrollIdentityModalLabels;
  accept: string;
  /** 实体 scope 绑定（可选）：传 { value, onChange } 启用该字段 */
  scope?: { value: string; onChange: (v: string) => void };
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [file, setFile] = useState<File | null>(null);
  useEffect(() => {
    if (open) {
      setName("");
      setRole("");
      setFile(null);
    }
  }, [open]);
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={labels.title}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>{t("common:cancel")}</Button>
          <Button loading={pending} disabled={!name.trim() || !file}
            onClick={() => file && onSubmit(name.trim(), role.trim(), file)}>
            {labels.enrollLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Input placeholder={labels.namePlaceholder} value={name}
          onChange={(e) => setName(e.target.value)} />
        <Input placeholder={labels.rolePlaceholder} value={role}
          onChange={(e) => setRole(e.target.value)} />
        {scope && labels.scopePlaceholder && (
          <Input className="font-mono" placeholder={labels.scopePlaceholder}
            value={scope.value} onChange={(e) => scope.onChange(e.target.value)} />
        )}
        <input
          type="file"
          accept={accept}
          className="text-sm text-muted file:mr-3 file:rounded-md file:border file:border-border file:bg-elevated file:px-3 file:py-1.5 file:text-sm file:text-foreground"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <p className="text-xs text-muted">{labels.hint}</p>
      </div>
    </Modal>
  );
}
