import ash from "./themes/ash";
import cappucino from "./themes/cappucino";
import midnightPlum from "./themes/midnightPlum";
import sunsetCitrus from "./themes/sunsetCitrus";
import spring from "./themes/spring";

export const DEFAULT_VISUAL_THEME = "spring";
export const THEME_SLOT_KEYS = [
  "c1Light",
  "c1Med",
  "c1Dark",
  "c2Light",
  "c2Med",
  "c2Dark",
  "veryLight",
  "veryDark",
];

export const VISUAL_THEME_OPTIONS = [
  { value: "spring", label: "Spring" },
  { value: "ash", label: "Ash" },
  { value: "cappucino", label: "Cappucino" },
  { value: "sunset-citrus", label: "Sunset Citrus" },
  { value: "midnight-plum", label: "Midnight Plum" },
];

const builtInThemes = {
  spring,
  ash,
  cappucino,
  "sunset-citrus": sunsetCitrus,
  "midnight-plum": midnightPlum,
};

let runtimeCustomThemes = {};

const buildCustomPalette = (slots) => ({
  white: slots.veryLight,
  softWhite: slots.veryLight,
  mintGreen: slots.c2Light,
  pearGreen: slots.c2Med,
  black: slots.veryDark,
  purple: slots.c1Med,
  deepPurple: slots.c1Dark,
  indigo: slots.c1Dark,
  lavender: slots.c1Light,
  lavenderDark: slots.c1Light,
  petrolBlue: slots.veryDark,
  violetGlow: slots.c2Light,
});

const buildCustomGradients = (slots) => ({
  light: [
    `radial-gradient(1200px 600px at 20% 10%, color-mix(in oklab, ${slots.c1Light} 18%, transparent), transparent 60%)`,
    `radial-gradient(900px 500px at 85% 20%, color-mix(in oklab, ${slots.c2Light} 18%, transparent), transparent 60%)`,
    `radial-gradient(800px 400px at 40% 85%, color-mix(in oklab, ${slots.c1Light} 20%, transparent), transparent 70%)`,
    `linear-gradient(145deg, color-mix(in oklab, ${slots.veryLight} 88%, ${slots.c1Light} 12%), color-mix(in oklab, ${slots.veryLight} 82%, ${slots.c2Light} 18%))`,
  ].join(", "),
  dark: [
    `radial-gradient(1500px 780px at -8% 32%, color-mix(in oklab, ${slots.c1Dark} 58%, rgba(0, 0, 0, 0.52)), transparent 72%)`,
    `radial-gradient(1460px 760px at 108% 18%, color-mix(in oklab, ${slots.c1Med} 40%, rgba(18, 14, 44, 0.56)), transparent 74%)`,
    `radial-gradient(1200px 820px at 42% 118%, color-mix(in oklab, ${slots.c2Med} 28%, rgba(0, 0, 0, 0.4)), transparent 76%)`,
    `radial-gradient(940px 600px at -4% 116%, color-mix(in oklab, ${slots.c2Dark} 34%, rgba(0, 0, 0, 0.52)), transparent 84%)`,
    `linear-gradient(210deg, color-mix(in oklab, ${slots.veryDark} 96%, black 4%), color-mix(in oklab, ${slots.c1Dark} 74%, ${slots.veryDark} 26%))`,
  ].join(", "),
});

export const buildCustomTheme = (theme) => {
  const slots = THEME_SLOT_KEYS.reduce((acc, key) => {
    acc[key] = String(theme?.slots?.[key] || "").trim();
    return acc;
  }, {});
  return {
    slots,
    palette: buildCustomPalette(slots),
    gradients: buildCustomGradients(slots),
    label: theme?.label || theme?.id || "Custom Theme",
  };
};

export const registerCustomThemes = (themes) => {
  const nextThemes = {};
  for (const item of Array.isArray(themes) ? themes : []) {
    const themeId = String(item?.id || "").trim().toLowerCase();
    if (!themeId) continue;
    const slotsValid = THEME_SLOT_KEYS.every(
      (key) => typeof item?.slots?.[key] === "string" && item.slots[key],
    );
    if (!slotsValid) continue;
    nextThemes[themeId] = buildCustomTheme(item);
  }
  runtimeCustomThemes = nextThemes;
};

export const getVisualThemes = () => ({
  ...builtInThemes,
  ...runtimeCustomThemes,
});

export const getVisualThemeOptions = (customThemes = []) => [
  ...VISUAL_THEME_OPTIONS,
  ...customThemes.map((theme) => ({
    value: String(theme.id || "").trim().toLowerCase(),
    label: String(theme.label || theme.id || "Custom Theme").trim(),
  })),
];

export const isBuiltInVisualTheme = (value) =>
  Object.prototype.hasOwnProperty.call(builtInThemes, String(value || "").trim().toLowerCase());

const CSS_SLOT_VARIABLES = {
  c1Light: "--theme-c1-light",
  c1Med: "--theme-c1-med",
  c1Dark: "--theme-c1-dark",
  c2Light: "--theme-c2-light",
  c2Med: "--theme-c2-med",
  c2Dark: "--theme-c2-dark",
  veryLight: "--theme-very-light",
  veryDark: "--theme-very-dark",
};

const CSS_COLOR_VARIABLES = {
  white: "--color-white",
  softWhite: "--color-soft-white",
  mintGreen: "--color-mint-green",
  pearGreen: "--color-pear-green",
  black: "--color-black",
  purple: "--color-purple",
  deepPurple: "--color-purple-deep",
  indigo: "--color-indigo",
  lavender: "--color-lavender",
  lavenderDark: "--color-lavender-dark",
  petrolBlue: "--color-petrol",
  violetGlow: "--color-violet-glow",
};

export const normalizeVisualTheme = (value) => {
  const raw = value == null ? "" : String(value).trim().toLowerCase();
  const normalized = raw.replace(/[\s_]+/g, "-");
  const aliases = {
    cappuccino: "cappucino",
    "sunset citrus": "sunset-citrus",
    sunsetcitrus: "sunset-citrus",
    "midnight plum": "midnight-plum",
    midnightplum: "midnight-plum",
  };
  const themeName = aliases[normalized] || normalized;
  return Object.prototype.hasOwnProperty.call(getVisualThemes(), themeName)
    ? themeName
    : DEFAULT_VISUAL_THEME;
};

export const getVisualTheme = (value) => getVisualThemes()[normalizeVisualTheme(value)];

export const getMuiPaletteOptions = (value, mode) => {
  const themes = getVisualThemes();
  const palette = getVisualTheme(value).palette || themes[DEFAULT_VISUAL_THEME].palette;
  const themeMode = mode === "dark" ? "dark" : "light";
  return {
    mode: themeMode,
    primary:
      themeMode === "dark"
        ? {
            main: palette.pearGreen,
            light: palette.mintGreen,
            dark: palette.mintGreen,
            contrastText: palette.black,
          }
        : {
            main: palette.purple,
            light: palette.lavender,
            dark: palette.deepPurple,
            contrastText: palette.white,
          },
    secondary:
      themeMode === "dark"
        ? {
            main: palette.lavender,
            contrastText: palette.black,
          }
        : {
            main: palette.pearGreen,
            contrastText: palette.black,
          },
    text: {
      primary: themeMode === "dark" ? palette.white : palette.black,
      secondary: themeMode === "dark" ? palette.lavender : palette.lavenderDark,
    },
  };
};

export const applyVisualTheme = (root, value, mode) => {
  if (!root || !root.style) return;
  const themeName = normalizeVisualTheme(value);
  const themeMode = mode === "dark" ? "dark" : "light";
  const themes = getVisualThemes();
  const theme = getVisualTheme(themeName) || themes[DEFAULT_VISUAL_THEME];
  const slots = theme.slots || {};
  const palette = theme.palette || {};
  const gradients = theme.gradients || themes[DEFAULT_VISUAL_THEME].gradients;

  Object.entries(CSS_SLOT_VARIABLES).forEach(([token, cssVariable]) => {
    if (typeof slots[token] === "string" && slots[token]) {
      root.style.setProperty(cssVariable, slots[token]);
    }
  });

  Object.entries(CSS_COLOR_VARIABLES).forEach(([token, cssVariable]) => {
    if (typeof palette[token] === "string" && palette[token]) {
      root.style.setProperty(cssVariable, palette[token]);
    }
  });
  root.style.setProperty("--gradient-background", gradients[themeMode]);
  root.dataset.visualTheme = themeName;
};

export default getVisualThemes;
