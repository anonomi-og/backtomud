let socket = io();

const roomNameEl = document.getElementById("room-name");
const roomDescEl = document.getElementById("room-desc");
const coordsEl = document.getElementById("coords");
const playersListEl = document.getElementById("players-list");
const messagesEl = document.getElementById("messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const moveButtons = document.querySelectorAll(".movement button");
const hpEl = document.getElementById("stat-hp");
const attackBonusEl = document.getElementById("stat-atk");
const acEl = document.getElementById("stat-ac");
const profEl = document.getElementById("stat-prof");
const weaponEl = document.getElementById("stat-weapon");
const charSummaryEl = document.getElementById("char-summary");
const abilityTableBody = document.getElementById("ability-table-body");

const ABILITY_ORDER = ["str", "dex", "con", "int", "wis", "cha"];
const ABILITY_LABELS = {
  str: "STR",
  dex: "DEX",
  con: "CON",
  int: "INT",
  wis: "WIS",
  cha: "CHA",
};

function addMessage(text, cssClass) {
  const div = document.createElement("div");
  div.textContent = text;
  if (cssClass) div.classList.add(cssClass);
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

socket.on("connect", () => {
  addMessage("Connected to server.", "system");
  socket.emit("join_game");
});

socket.on("connected", (data) => {
  // Optional extra greeting from server
  if (data && data.message) {
    addMessage(data.message, "system");
  }
});

socket.on("room_state", (data) => {
  roomNameEl.textContent = data.room_name;
  roomDescEl.textContent = data.description;
  coordsEl.textContent = `Position: (${data.x}, ${data.y})`;

  playersListEl.innerHTML = "";
  (data.players || []).forEach((name) => {
    const li = document.createElement("li");
    li.textContent = name;
    if (name === USERNAME) li.classList.add("you");
    playersListEl.appendChild(li);
  });

  if (data.character) {
    renderCharacterPanel(data.character);
  }
});

socket.on("system_message", (data) => {
  if (data && data.text) {
    addMessage(data.text, "system");
  }
});

socket.on("chat_message", (data) => {
  if (!data) return;
  const from = data.from || "??";
  const text = data.text || "";
  addMessage(`${from}: ${text}`, "chat");
});

socket.on("disconnect", () => {
  addMessage("Disconnected from server.", "system");
});

// Movement buttons
moveButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const dir = btn.getAttribute("data-dir");
    socket.emit("move", { direction: dir });
  });
});

// Chat form
chatForm.addEventListener("submit", (evt) => {
  evt.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;
  socket.emit("chat", { text });
  chatInput.value = "";
});

function renderCharacterPanel(character) {
  if (charSummaryEl) {
    const identity = [character.race, character.char_class].filter(Boolean).join(" ");
    const levelLabel = character.level ? ` (Level ${character.level})` : "";
    charSummaryEl.textContent = `${identity || "Unknown"}${levelLabel}`;
  }
  if (hpEl && typeof character.hp !== "undefined" && typeof character.max_hp !== "undefined") {
    hpEl.textContent = `${character.hp} / ${character.max_hp}`;
  }
  if (acEl && typeof character.ac !== "undefined") {
    acEl.textContent = character.ac;
  }
  if (profEl && typeof character.proficiency !== "undefined") {
    profEl.textContent = formatBonus(character.proficiency);
  }
  if (weaponEl && character.weapon) {
    const weapon = character.weapon;
    const damageType = weapon.damage_type ? ` ${weapon.damage_type}` : "";
    weaponEl.textContent = `${weapon.name} (${weapon.dice}${damageType})`;
  }
  if (attackBonusEl && typeof character.attack_bonus !== "undefined") {
    const abilityTag = character.attack_ability ? character.attack_ability.toUpperCase() : "";
    attackBonusEl.textContent = `${formatBonus(character.attack_bonus)}${abilityTag ? ` via ${abilityTag}` : ""}`;
  }
  renderAbilityTable(character.abilities, character.ability_mods);
}

function renderAbilityTable(scores = {}, modifiers = {}) {
  if (!abilityTableBody) return;
  abilityTableBody.innerHTML = "";
  ABILITY_ORDER.forEach((ability) => {
    const score = scores[ability];
    const mod = modifiers[ability];
    const tr = document.createElement("tr");
    const labelCell = document.createElement("td");
    labelCell.textContent = ABILITY_LABELS[ability];

    const scoreCell = document.createElement("td");
    scoreCell.textContent = typeof score === "number" ? score : "--";

    const modCell = document.createElement("td");
    modCell.textContent = typeof mod === "number" ? formatBonus(mod) : "--";

    tr.appendChild(labelCell);
    tr.appendChild(scoreCell);
    tr.appendChild(modCell);
    abilityTableBody.appendChild(tr);
  });
}

function formatBonus(value) {
  if (typeof value !== "number") return "--";
  return value >= 0 ? `+${value}` : `${value}`;
}
