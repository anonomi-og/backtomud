let socket = io();

const roomNameEl = document.getElementById("room-name");
const roomDescEl = document.getElementById("room-desc");
const coordsEl = document.getElementById("coords");
const playersListEl = document.getElementById("players-list");
const mobListEl = document.getElementById("mob-list");
const lootListEl = document.getElementById("loot-list");
const messagesEl = document.getElementById("messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const moveButtons = document.querySelectorAll(".movement button");
const doorListEl = document.getElementById("door-list");
const warpButton = document.getElementById("warp-button");
const warpNoteEl = document.getElementById("warp-note");
const hpEl = document.getElementById("stat-hp");
const attackBonusEl = document.getElementById("stat-atk");
const acEl = document.getElementById("stat-ac");
const profEl = document.getElementById("stat-prof");
const weaponEl = document.getElementById("stat-weapon");
const goldEl = document.getElementById("stat-gold");
const xpEl = document.getElementById("stat-xp");
const charSummaryEl = document.getElementById("char-summary");
const abilityTableBody = document.getElementById("ability-table-body");
const weaponSelect = document.getElementById("weapon-select");
const weaponListEl = document.getElementById("weapon-list");
const equipForm = document.getElementById("equip-form");
const equipButton = equipForm ? equipForm.querySelector("button") : null;
const spellSelect = document.getElementById("spell-select");
const spellListEl = document.getElementById("spell-list");
const spellTargetInput = document.getElementById("spell-target");
const castForm = document.getElementById("cast-form");
const castButton = castForm ? castForm.querySelector("button") : null;
const effectsListEl = document.getElementById("effects-list");
const itemListEl = document.getElementById("item-list");

let lastSpellList = [];

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
  renderMovementControls(data.exits || {});

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
  renderMobList(data.mobs || []);
  renderLootList(data.loot || []);
  renderDoorList(data.doors || []);
  renderWarpStone(data.warp_stone || null);
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

if (warpButton) {
  warpButton.addEventListener("click", () => {
    socket.emit("activate_warp");
  });
}

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
    const summaryParts = [];
    if (character.name) summaryParts.push(character.name);
    const lineage = [character.race, character.char_class].filter(Boolean).join(" ");
    if (lineage) summaryParts.push(lineage);
    if (character.level) summaryParts.push(`Level ${character.level}`);
    charSummaryEl.textContent = summaryParts.join(" • ") || "Unknown adventurer";
  }
  const bioEl = document.getElementById("character-bio");
  if (bioEl) {
    bioEl.textContent = character.bio || "";
    bioEl.hidden = !bioEl.textContent;
  }
  const descriptionEl = document.getElementById("character-description");
  if (descriptionEl) {
    descriptionEl.textContent = character.description || "";
    descriptionEl.hidden = !descriptionEl.textContent;
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
  if (goldEl && typeof character.gold !== "undefined") {
    goldEl.textContent = character.gold;
  }
  if (xpEl && typeof character.xp !== "undefined") {
    xpEl.textContent = character.xp;
  }
  if (attackBonusEl && typeof character.attack_bonus !== "undefined") {
    const abilityTag = character.attack_ability ? character.attack_ability.toUpperCase() : "";
    attackBonusEl.textContent = `${formatBonus(character.attack_bonus)}${abilityTag ? ` via ${abilityTag}` : ""}`;
  }
  renderAbilityTable(character.abilities, character.ability_mods);
  renderWeaponPanel(character.weapon_inventory || [], character.weapon);
  renderSpellPanel(character.spells || []);
  renderEffectsPanel(character.effects || []);
  renderItemPanel(character.items || []);
}

function renderMovementControls(exits = {}) {
  moveButtons.forEach((btn) => {
    const dir = btn.getAttribute("data-dir");
    const exitInfo = exits[dir] || {};
    const canMove = !!exitInfo.available;
    btn.disabled = !canMove;
    if (exitInfo.reason) {
      btn.setAttribute("title", exitInfo.reason);
    } else {
      btn.removeAttribute("title");
    }
  });
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

function renderDoorList(doors = []) {
  if (!doorListEl) return;
  doorListEl.innerHTML = "";
  if (!doors.length) {
    const li = document.createElement("li");
    li.textContent = "No doors are within reach.";
    li.classList.add("empty");
    doorListEl.appendChild(li);
    return;
  }

  doors.forEach((door) => {
    const li = document.createElement("li");
    li.classList.add("door-entry");

    const titleRow = document.createElement("div");
    titleRow.classList.add("door-title");
    titleRow.textContent = door.name || "Door";

    const stateSpan = document.createElement("span");
    stateSpan.classList.add("door-state");
    stateSpan.textContent = door.is_open ? "Open" : "Closed";
    titleRow.appendChild(stateSpan);
    li.appendChild(titleRow);

    const directionRow = document.createElement("div");
    directionRow.classList.add("door-direction");
    const facingLabel = door.facing ? door.facing.toUpperCase() : null;
    const destination = door.other_side && door.other_side.room_name ? door.other_side.room_name : "the adjacent room";
    if (facingLabel) {
      directionRow.textContent = `Faces ${facingLabel} toward ${destination}.`;
    } else {
      directionRow.textContent = `Leads toward ${destination}.`;
    }
    li.appendChild(directionRow);

    if (door.description) {
      const desc = document.createElement("div");
      desc.classList.add("door-desc");
      desc.textContent = door.description;
      li.appendChild(desc);
    }

    const actions = document.createElement("div");
    actions.classList.add("door-actions");

    const openButton = document.createElement("button");
    openButton.textContent = "Open";
    openButton.disabled = !!door.is_open;
    openButton.addEventListener("click", () => {
      socket.emit("door_action", { door_id: door.id, action: "open" });
    });

    const closeButton = document.createElement("button");
    closeButton.textContent = "Close";
    closeButton.disabled = !door.is_open;
    closeButton.addEventListener("click", () => {
      socket.emit("door_action", { door_id: door.id, action: "close" });
    });

    actions.appendChild(openButton);
    actions.appendChild(closeButton);
    li.appendChild(actions);

    doorListEl.appendChild(li);
  });
}

function renderWarpStone(warpInfo) {
  if (!warpButton || !warpNoteEl) return;
  if (warpInfo) {
    warpButton.hidden = false;
    warpButton.disabled = false;
    warpButton.textContent = `Activate ${warpInfo.label || "Warp Stone"}`;
    warpNoteEl.textContent = warpInfo.description || "A warp stone hums nearby.";
  } else {
    warpButton.hidden = true;
    warpNoteEl.textContent = "No warp stone resonates here.";
  }
}

function formatBonus(value) {
  if (typeof value !== "number") return "--";
  return value >= 0 ? `+${value}` : `${value}`;
}

function renderWeaponPanel(weaponInventory = [], equippedWeapon = null) {
  if (!weaponSelect || !weaponListEl) return;
  weaponSelect.innerHTML = "";
  weaponListEl.innerHTML = "";

  if (!weaponInventory.length) {
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "No weapons available";
    weaponSelect.appendChild(placeholder);
    weaponSelect.disabled = true;
    if (equipButton) equipButton.disabled = true;
    return;
  }

  weaponSelect.disabled = false;
  if (equipButton) equipButton.disabled = false;

  weaponInventory.forEach((weapon) => {
    const label = formatWeaponLabel(weapon);
    const option = document.createElement("option");
    option.value = weapon.key;
    option.textContent = label;
    if (weapon.equipped) option.selected = true;
    weaponSelect.appendChild(option);

    const li = document.createElement("li");
    li.textContent = label;
    if (weapon.equipped) li.classList.add("equipped");
    weaponListEl.appendChild(li);
  });
}

function formatWeaponLabel(weapon) {
  if (!weapon) return "--";
  const damage = weapon.dice ? weapon.dice : "-";
  const type = weapon.damage_type ? ` ${weapon.damage_type}` : "";
  const suffix = weapon.equipped ? " [Equipped]" : "";
  return `${weapon.name} (${damage}${type})${suffix}`;
}

function renderSpellPanel(spellList = []) {
  if (!spellSelect || !spellListEl) return;
  const previousValue = spellSelect.value;
  lastSpellList = Array.isArray(spellList) ? [...spellList] : [];
  spellSelect.innerHTML = "";
  spellListEl.innerHTML = "";

  if (!spellList.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No spells or abilities";
    spellSelect.appendChild(option);
    spellSelect.disabled = true;
    if (castButton) castButton.disabled = true;
    if (spellTargetInput) {
      spellTargetInput.value = "";
      spellTargetInput.placeholder = "Target (optional)";
      spellTargetInput.disabled = true;
    }
    return;
  }

  spellSelect.disabled = false;
  let selectedValue = null;
  const readySpell = spellList.find((spell) => spell.key === previousValue && !spell.cooldown_remaining);
  if (readySpell) {
    selectedValue = readySpell.key;
  }

  spellList.forEach((spell) => {
    const option = document.createElement("option");
    option.value = spell.key;
    option.textContent = formatSpellOption(spell);
    if (spell.cooldown_remaining) {
      option.disabled = true;
    }
    spellSelect.appendChild(option);

    const li = document.createElement("li");
    li.textContent = formatSpellListEntry(spell);
    if (spell.cooldown_remaining) {
      li.classList.add("on-cooldown");
    }
    spellListEl.appendChild(li);
  });

  if (!selectedValue) {
    const firstAvailable = spellList.find((spell) => !spell.cooldown_remaining);
    if (firstAvailable) {
      selectedValue = firstAvailable.key;
    } else {
      selectedValue = spellList[0].key;
    }
  }

  if (selectedValue) {
    spellSelect.value = selectedValue;
  }

  const canCast = spellList.some((spell) => !spell.cooldown_remaining);
  if (castButton) castButton.disabled = !canCast;
  if (spellTargetInput) {
    spellTargetInput.disabled = false;
  }
  updateSpellTargetField(lastSpellList, spellSelect.value);
}

function formatSpellOption(spell) {
  if (!spell) return "--";
  const typeLabel = spell.type || "Ability";
  if (spell.cooldown_remaining) {
    return `${spell.name} (${typeLabel}, ${spell.cooldown_remaining}s)`;
  }
  return `${spell.name} (${typeLabel} ready)`;
}

function formatSpellListEntry(spell) {
  if (!spell) return "--";
  const typeLabel = spell.type ? `${spell.type}: ` : "";
  const description = spell.description || "";
  let cooldownText = "";
  if (spell.cooldown_remaining) {
    cooldownText = ` (recharges in ${spell.cooldown_remaining}s)`;
  } else if (spell.cooldown) {
    cooldownText = ` (${spell.cooldown}s cooldown)`;
  }
  return `${spell.name} — ${typeLabel}${description}${cooldownText}`;
}

function updateSpellTargetField(spellList, selectedKey) {
  if (!spellTargetInput) return;
  const spell = (spellList || []).find((item) => item.key === selectedKey);
  if (!spell) {
    spellTargetInput.placeholder = "Target (optional)";
    spellTargetInput.disabled = !spellList || !spellList.length;
    if (spellTargetInput.disabled) {
      spellTargetInput.value = "";
    }
    return;
  }

  const targetType = spell.target || "any";
  if (targetType === "self" || targetType === "none") {
    spellTargetInput.value = "";
    spellTargetInput.placeholder = "No target needed";
    spellTargetInput.disabled = true;
  } else if (targetType === "enemy") {
    spellTargetInput.disabled = false;
    spellTargetInput.placeholder = "Target name required";
  } else {
    spellTargetInput.disabled = false;
    spellTargetInput.placeholder = "Target (optional)";
  }
}

function renderEffectsPanel(effects = []) {
  if (!effectsListEl) return;
  effectsListEl.innerHTML = "";
  if (!effects.length) {
    const li = document.createElement("li");
    li.textContent = "None";
    effectsListEl.appendChild(li);
    return;
  }
  effects.forEach((effect) => {
    const li = document.createElement("li");
    const name = effect.name || "Unnamed effect";
    const description = effect.description ? ` — ${effect.description}` : "";
    const timer =
      typeof effect.expires_in === "number" && effect.expires_in > 0
        ? ` (${effect.expires_in}s)`
        : "";
    li.textContent = `${name}${description}${timer}`;
    effectsListEl.appendChild(li);
  });
}

function renderItemPanel(items = []) {
  if (!itemListEl) return;
  itemListEl.innerHTML = "";
  if (!items.length) {
    const li = document.createElement("li");
    li.textContent = "No notable gear.";
    itemListEl.appendChild(li);
    return;
  }
  items.forEach((item) => {
    const li = document.createElement("li");
    const name = item.name || "Unknown item";
    const rarity = item.rarity ? ` [${item.rarity}]` : "";
    const description = item.description ? ` — ${item.description}` : "";
    li.textContent = `${name}${rarity}${description}`;
    itemListEl.appendChild(li);
  });
}

function renderMobList(mobs = []) {
  if (!mobListEl) return;
  mobListEl.innerHTML = "";
  if (!mobs.length) {
    const li = document.createElement("li");
    li.textContent = "No hostile presences.";
    mobListEl.appendChild(li);
    return;
  }
  mobs.forEach((mob) => {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = `${mob.name} (${mob.hp}/${mob.max_hp} HP, AC ${mob.ac})`;
    li.appendChild(label);
    const attackButton = document.createElement("button");
    attackButton.textContent = "Attack";
    attackButton.addEventListener("click", () => {
      socket.emit("chat", { text: `/attack ${mob.id}` });
    });
    li.appendChild(attackButton);
    mobListEl.appendChild(li);
  });
}

function renderLootList(loot = []) {
  if (!lootListEl) return;
  lootListEl.innerHTML = "";
  if (!loot.length) {
    const li = document.createElement("li");
    li.textContent = "Nothing of value lies here.";
    lootListEl.appendChild(li);
    return;
  }
  loot.forEach((entry) => {
    const li = document.createElement("li");
    const name = entry.name || "Unmarked loot";
    const desc = entry.description ? ` — ${entry.description}` : "";
    const amount = entry.type === "gold" && entry.amount ? ` (${entry.amount} gold)` : "";
    const label = document.createElement("span");
    label.textContent = `${entry.id}: ${name}${amount}${desc}`;
    li.appendChild(label);
    const takeButton = document.createElement("button");
    takeButton.textContent = "Take";
    takeButton.addEventListener("click", () => {
      socket.emit("pickup_loot", { loot_id: entry.id });
    });
    li.appendChild(takeButton);
    lootListEl.appendChild(li);
  });
}

if (equipForm) {
  equipForm.addEventListener("submit", (evt) => {
    evt.preventDefault();
    if (!weaponSelect || !weaponSelect.value) return;
    socket.emit("equip_weapon", { weapon: weaponSelect.value });
  });
}

if (castForm) {
  castForm.addEventListener("submit", (evt) => {
    evt.preventDefault();
    if (!spellSelect || !spellSelect.value) return;
    const selectedOption = spellSelect.options[spellSelect.selectedIndex];
    if (selectedOption && selectedOption.disabled) {
      return;
    }
    if (castButton && castButton.disabled) {
      return;
    }
    const targetValue = spellTargetInput && !spellTargetInput.disabled ? spellTargetInput.value.trim() : "";
    const payload = { spell: spellSelect.value };
    if (targetValue) {
      payload.target = targetValue;
    }
    socket.emit("cast_spell", payload);
    if (spellTargetInput && !spellTargetInput.disabled) {
      spellTargetInput.value = "";
    }
  });
}

if (spellSelect) {
  spellSelect.addEventListener("change", () => {
    updateSpellTargetField(lastSpellList, spellSelect.value);
  });
}
