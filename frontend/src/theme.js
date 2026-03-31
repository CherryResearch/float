import cappucino from "./themes/cappucino";
import midnightPlum from "./themes/midnightPlum";
import sunsetCitrus from "./themes/sunsetCitrus";
import spring from "./themes/spring";

export const DEFAULT_VISUAL_THEME = "spring";

export const VISUAL_THEME_OPTIONS = [
  { value: "spring", label: "Spring" },
  { value: "cappucino", label: "Cappucino" },
  { value: "sunset-citrus", label: "Sunset Citrus" },
  { value: "midnight-plum", label: "Midnight Plum" },
];

export const visualThemes = {
  spring,
  cappucino,
  "sunset-citrus": sunsetCitrus,
  "midnight-plum": midnightPlum,
};

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
  return Object.prototype.hasOwnProperty.call(visualThemes, themeName)
    ? themeName
    : DEFAULT_VISUAL_THEME;
};

export const getVisualTheme = (value) => visualThemes[normalizeVisualTheme(value)];

export const getMuiPaletteOptions = (value, mode) => {
  const palette = getVisualTheme(value).palette || visualThemes[DEFAULT_VISUAL_THEME].palette;
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
  const theme = getVisualTheme(themeName) || visualThemes[DEFAULT_VISUAL_THEME];
  const slots = theme.slots || {};
  const palette = theme.palette || {};
  const gradients = theme.gradients || visualThemes[DEFAULT_VISUAL_THEME].gradients;

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

export default visualThemes;
