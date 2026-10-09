import { Modal, type ModalProps } from "../ui/Modal";

export type DrawerProps = Omit<ModalProps, "placement">;

export function Drawer({ width = "max-w-lg", ...props }: DrawerProps) {
  return <Modal {...props} width={width} placement="right" />;
}
