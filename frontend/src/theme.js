// Keep in sync with CSS variables defined in styles/theme.css
export const palette = {
  white: '#ffffff',
  softWhite: '#f6f8fb',
  mintGreen: '#22c55e',
  pearGreen: '#16a34a',
  black: '#0b1220',
  purple: '#4f46e5',
  deepPurple: '#3730a3',
  indigo: '#390892', // used in dark mode styling elsewhere
  lavender: '#f3f0ff',
  lavenderDark: '#e2e8f0',
  petrolBlue: '#051427',
  violetGlow: '#9f8df0',
};

const lightLayers = [
  `radial-gradient(1200px 600px at 20% 10%, color-mix(in oklab, ${palette.purple} 12%, transparent), transparent 60%)`,
  `radial-gradient(900px 500px at 85% 20%, color-mix(in oklab, ${palette.mintGreen} 14%, transparent), transparent 60%)`,
  `radial-gradient(800px 400px at 40% 85%, color-mix(in oklab, ${palette.lavender} 16%, transparent), transparent 70%)`,
  `linear-gradient(145deg, color-mix(in oklab, ${palette.lavender} 75%, ${palette.white} 25%), color-mix(in oklab, ${palette.softWhite} 90%, #fff4f8 10%))`,
];

const darkLayers = [
  `radial-gradient(1500px 780px at -8% 32%, color-mix(in oklab, ${palette.indigo} 52%, rgba(0, 0, 0, 0.45)), transparent 72%)`,
  `radial-gradient(1500px 760px at 110% 22%, color-mix(in oklab, ${palette.violetGlow} 60%, rgba(39, 7, 73, 0.55)), transparent 74%)`,
  `radial-gradient(1200px 840px at 40% 118%, color-mix(in oklab, ${palette.deepPurple} 52%, rgba(0, 0, 0, 0.4)), transparent 75%)`,
  `radial-gradient(900px 580px at -6% 118%, color-mix(in oklab, ${palette.purple} 48%, rgba(0, 0, 0, 0.5)), transparent 85%)`,
  `radial-gradient(820px 520px at 6% -3%, rgba(255, 255, 255, 0.08), transparent 80%)`,
  `radial-gradient(620px 420px at 96% 98%, rgba(227, 218, 255, 0.12), transparent 86%)`,
  `linear-gradient(128deg, rgba(255, 255, 255, 0.07), rgba(255, 255, 255, 0) 48%, rgba(255, 255, 255, 0.05) 96%)`,
  `linear-gradient(210deg, rgba(2, 4, 10, 0.98), rgba(6, 7, 22, 0.92))`,
];

export const gradients = {
  light: lightLayers.join(", "),
  dark: darkLayers.join(", "),
};

export default { palette, gradients };
