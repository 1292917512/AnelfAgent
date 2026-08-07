/** 新增/编辑关系边的表单弹窗。 */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Input, Modal, Switch, Textarea } from "@/components/ui";
import type { GraphEdge } from "@/lib/types";

interface Props {
  open: boolean;
  onClose: () => void;
  /** 传入则为编辑模式（只允许改谓词/强度/证据/对称），否则为新增 */
  edge?: GraphEdge | null;
  /** 新增模式的预填主语（从节点抽屉发起时） */
  presetSubject?: string;
  onSubmit: (form: {
    subject: string; predicate: string; object: string;
    symmetric: boolean; strength: number; evidence: string;
  }) => Promise<void>;
}

export function RelationFormModal({ open, onClose, edge, presetSubject, onSubmit }: Props) {
  const { t } = useTranslation("graph");
  const editing = Boolean(edge);
  const [subject, setSubject] = useState("");
  const [predicate, setPredicate] = useState("");
  const [object, setObject] = useState("");
  const [symmetric, setSymmetric] = useState(false);
  const [strength, setStrength] = useState(0.7);
  const [evidence, setEvidence] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setSubject(edge?.subject.node_key ?? presetSubject ?? "");
    setPredicate(edge?.predicate ?? "");
    setObject(edge?.object.node_key ?? "");
    setSymmetric(edge?.symmetric ?? false);
    setStrength(edge?.strength ?? 0.7);
    setEvidence(edge?.evidence ?? "");
  }, [open, edge, presetSubject]);

  const valid = predicate.trim() && (editing || (subject.trim() && object.trim()));

  const handleSubmit = async () => {
    if (!valid || submitting) return;
    setSubmitting(true);
    try {
      await onSubmit({
        subject: subject.trim(), predicate: predicate.trim(), object: object.trim(),
        symmetric, strength, evidence: evidence.trim(),
      });
      onClose();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? t("form.editTitle") : t("form.addTitle")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>{t("form.cancel")}</Button>
          <Button onClick={handleSubmit} disabled={!valid || submitting}>
            {submitting ? t("form.submitting") : t("form.confirm")}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="mb-1 block text-xs text-muted">{t("form.subject")}</span>
            <Input value={subject} disabled={editing} placeholder="user:qq:123 / person:老王"
              onChange={(e) => setSubject(e.target.value)} />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-muted">{t("form.object")}</span>
            <Input value={object} disabled={editing} placeholder="topic:火锅"
              onChange={(e) => setObject(e.target.value)} />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="mb-1 block text-xs text-muted">{t("form.predicate")}</span>
            <Input value={predicate} placeholder={t("form.predicateHint")}
              onChange={(e) => setPredicate(e.target.value)} />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-muted">
              {t("form.strength")}：{strength.toFixed(2)}
            </span>
            <input type="range" min={0} max={1} step={0.05} value={strength}
              className="mt-2 w-full accent-[var(--accent)]"
              onChange={(e) => setStrength(Number(e.target.value))} />
          </label>
        </div>
        <label className="flex items-center gap-2 text-sm text-heading">
          <Switch checked={symmetric} onChange={setSymmetric} />
          {t("form.symmetric")}
        </label>
        <label className="block">
          <span className="mb-1 block text-xs text-muted">{t("form.evidence")}</span>
          <Textarea rows={2} value={evidence} placeholder={t("form.evidenceHint")}
            onChange={(e) => setEvidence(e.target.value)} />
        </label>
      </div>
    </Modal>
  );
}
