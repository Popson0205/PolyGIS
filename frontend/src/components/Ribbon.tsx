import type { ReactNode } from "react";

/** A group of a mode's tools: its name on top, its buttons under it, two rows high. */
export function Group({ name, children }: { name: string; children: ReactNode }) {
  return <div className="ws-group" role="group" aria-label={name}><div className="ws-group-name" aria-hidden="true">{name}</div><div className="ws-group-body">{children}</div></div>;
}
/** A large ribbon button: an icon over its label. */
export function Tb({ icon, label, onClick, on, disabled, title }: { icon: ReactNode; label: string; onClick: () => void; on?: boolean; disabled?: boolean; title?: string }) {
  return <button type="button" className={`ws-tb${on ? " is-on" : ""}`} onClick={onClick} disabled={disabled} aria-pressed={on} title={title}><span aria-hidden="true" className="ws-tb-icon">{icon}</span>{label}</button>;
}

/** A small ribbon button (icon and label on one line), stacked three high beside the large ones. */
export function Ts({ icon, label, onClick, on, disabled, title }: { icon: ReactNode; label: string; onClick: () => void; on?: boolean; disabled?: boolean; title?: string }) {
  return <button type="button" className={`ws-tb-small${on ? " is-on" : ""}`} onClick={onClick} disabled={disabled} aria-pressed={on} title={title}><span aria-hidden="true" className="ws-tb-icon">{icon}</span>{label}</button>;
}
