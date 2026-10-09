import { TasksPanel } from "@/pages/tasks/TasksPanel";
import { PageContainer, PageIntro } from "@/components/common/PageContainer";

export default function Tasks() {
  return (
    <PageContainer>
      <PageIntro />
      <TasksPanel />
    </PageContainer>
  );
}
