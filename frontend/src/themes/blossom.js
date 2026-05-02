// Soft mint + blossom pink palette. Keeps the bright floral branch available as a built-in theme.
// Slots are the future-facing theme contract; palette keeps legacy semantic tokens alive.
export const slots = {
  c1Light: "#a8f5ab",
  c1Med: "#4aed1d",
  c1Dark: "#05420f",
  c2Light: "#f8f7f7",
  c2Med: "#f7bff3",
  c2Dark: "#d24ba1",
  veryLight: "#e8f5f7",
  veryDark: "#1e3038",
};

export const palette = {
  white: slots.veryLight,
  softWhite: "#f7fcfc",
  mintGreen: slots.c1Light,
  pearGreen: slots.c1Med,
  black: slots.veryDark,
  purple: slots.c2Dark,
  deepPurple: "#8f2967",
  indigo: "#24333d",
  lavender: slots.c2Med,
  lavenderDark: "#d98dc6",
  petrolBlue: slots.veryDark,
  violetGlow: "#ffd8f8",
};

const lightLayers = [
  `radial-gradient(1200px 620px at 16% 12%, color-mix(in oklab, ${slots.c1Light} 28%, transparent), transparent 64%)`,
  `radial-gradient(980px 520px at 86% 18%, color-mix(in oklab, ${slots.c2Med} 24%, transparent), transparent 68%)`,
  `radial-gradient(860px 500px at 48% 88%, color-mix(in oklab, ${slots.c2Light} 22%, transparent), transparent 74%)`,
  `linear-gradient(145deg, color-mix(in oklab, ${slots.veryLight} 84%, ${slots.c1Light} 16%), color-mix(in oklab, ${slots.c2Light} 14%, ${slots.veryLight} 86%))`,
];

const darkLayers = [
  `radial-gradient(1520px 820px at -10% 28%, color-mix(in oklab, ${slots.c1Dark} 62%, rgba(0, 0, 0, 0.58)), transparent 72%)`,
  `radial-gradient(1440px 760px at 108% 18%, color-mix(in oklab, ${slots.c2Dark} 42%, rgba(34, 10, 28, 0.5)), transparent 75%)`,
  `radial-gradient(1180px 760px at 44% 118%, color-mix(in oklab, ${slots.c1Med} 28%, rgba(0, 0, 0, 0.44)), transparent 78%)`,
  `radial-gradient(920px 580px at 4% -4%, rgba(168, 245, 171, 0.1), transparent 80%)`,
  `radial-gradient(700px 460px at 98% 98%, rgba(247, 191, 243, 0.14), transparent 86%)`,
  `linear-gradient(126deg, rgba(255, 255, 255, 0.07), rgba(255, 255, 255, 0) 38%, rgba(255, 255, 255, 0.03) 96%)`,
  `linear-gradient(210deg, rgba(30, 48, 56, 0.99), rgba(15, 25, 30, 0.97))`,
];

export const gradients = {
  light: lightLayers.join(", "),
  dark: darkLayers.join(", "),
};

export default { slots, palette, gradients };
