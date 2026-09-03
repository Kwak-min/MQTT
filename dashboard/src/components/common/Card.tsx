import type { CSSProperties, ReactNode } from 'react';

export function Card({
  title,
  titleEn,
  meta,
  actions,
  footer,
  children,
  style,
}: {
  title?: string;
  titleEn?: string;
  meta?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <section className="card" style={style}>
      {(title || meta || actions) && (
        <header className="card__head">
          {title && (
            <h2 className="card__title">
              {title}
              {titleEn ? <small> · {titleEn}</small> : null}
            </h2>
          )}
          {actions ?? (meta ? <span className="card__meta">{meta}</span> : null)}
        </header>
      )}
      {children}
      {footer ? <footer className="card__foot">{footer}</footer> : null}
    </section>
  );
}
