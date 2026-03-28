import React from "react";

const FilterBar = ({
  searchPlaceholder = "filter...",
  searchValue,
  onSearch,
  onSearchSubmit,
  children,
  right = null,
}) => {
  const handleKeyDown = (event) => {
    if (event.key === "Enter" && typeof onSearchSubmit === "function") {
      event.preventDefault();
      onSearchSubmit();
    }
  };

  return (
    <div
      className="filter-bar"
      style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}
    >
      <input
        type="search"
        placeholder={searchPlaceholder}
        value={searchValue}
        onChange={(event) => onSearch && onSearch(event.target.value)}
        onKeyDown={handleKeyDown}
        className="flex-input"
        style={{ minWidth: 180 }}
      />
      {children}
      {right ? <div className="filter-bar-spacer" style={{ marginLeft: "auto" }} /> : null}
      {right}
    </div>
  );
};

export default FilterBar;
