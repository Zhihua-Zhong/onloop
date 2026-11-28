// Stands in for `react-style-singleton` (vite.config.ts aliases it here). Radix's dialogs lock the page's scrolling with
// react-remove-scroll, which writes a <style> element into <head> to hide the body's scroll bar. onloopd's CSP allows
// styles from this origin only (`style-src 'self'`, decision 0009), so the browser refuses that element and reports it
// as an error. onloop's page never scrolls its body (html, body and the shell are the window's height; `main` scrolls),
// so those styles have nothing to do here: this version writes none. The lock's other half, which stops the wheel and
// touch from scrolling what is behind a dialog, is event listeners and works as before.

type Sheet = { add: (css: string) => void; remove: () => void };

export const stylesheetSingleton = (): Sheet => ({ add: () => undefined, remove: () => undefined });

export const styleHookSingleton = () => (_styles: string, _dynamic?: boolean) => undefined;

export const styleSingleton = () => {
  const Sheet = (_props: { styles: string; dynamic?: boolean }) => null;
  return Sheet;
};
