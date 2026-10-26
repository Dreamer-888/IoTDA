// Canvas and WebGL colors use the same tokens as the page controls.
export function readTheme() {
  const styles = getComputedStyle(document.documentElement);
  const tokens = ['surface', 'text', 'muted', 'line', 'border', 'border-strong', 'accent', 'blue', 'coral', 'chart-x', 'chart-y', 'chart-z', 'scene-grid'];
  return Object.fromEntries(tokens.map((token) => [token, styles.getPropertyValue('--' + token).trim()]));
}
