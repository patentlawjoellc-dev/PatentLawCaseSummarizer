const controls = {
  keyword: document.getElementById("filter-keyword"),
  origin: document.getElementById("filter-origin"),
  tag: document.getElementById("filter-tag"),
  from: document.getElementById("filter-from"),
  to: document.getElementById("filter-to"),
  clear: document.getElementById("filter-clear"),
  count: document.getElementById("filter-count"),
};

const cards = Array.from(document.querySelectorAll(".case-card"));

function normalize(value) {
  return String(value || "").trim().toLowerCase();
}

function applyFilters() {
  const keyword = normalize(controls.keyword.value);
  const origin = controls.origin.value;
  const tag = normalize(controls.tag.value);
  const from = controls.from.value;
  const to = controls.to.value;
  let visible = 0;

  cards.forEach((card) => {
    const date = card.dataset.date || "";
    const search = card.dataset.search || "";
    const tags = card.dataset.tags || "";
    const matches =
      (!keyword || search.includes(keyword)) &&
      (!origin || card.dataset.origin === origin) &&
      (!tag || tags.includes(tag)) &&
      (!from || date >= from) &&
      (!to || date <= to);

    card.hidden = !matches;
    if (matches) visible += 1;
  });

  document.querySelectorAll(".day").forEach((section) => {
    const sectionCards = Array.from(section.querySelectorAll(".case-card"));
    section.hidden = sectionCards.length > 0 && sectionCards.every((card) => card.hidden);
  });

  controls.count.textContent = `${visible} matching case${visible === 1 ? "" : "s"}`;
}

["keyword", "origin", "tag", "from", "to"].forEach((name) => {
  controls[name].addEventListener("input", applyFilters);
});

controls.clear.addEventListener("click", () => {
  controls.keyword.value = "";
  controls.origin.value = "";
  controls.tag.value = "";
  controls.from.value = "";
  controls.to.value = "";
  applyFilters();
});

applyFilters();
