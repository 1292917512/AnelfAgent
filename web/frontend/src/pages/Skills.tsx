import { useTranslation } from "react-i18next";
import { GraduationCap } from "lucide-react";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { SkillsPanel } from "@/pages/skills/SkillsPanel";

export default function Skills() {
  const { t } = useTranslation(["skills"]);

  return (
    <PageContainer wide>
      <PageHeader
        icon={<GraduationCap size={20} className="text-accent" />}
        title={t("skills:title")}
        subtitle={t("skills:subtitle")}
      />
      <SkillsPanel />
    </PageContainer>
  );
}
