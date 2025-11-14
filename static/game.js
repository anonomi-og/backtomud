let socket = io();

const roomNameEl = document.getElementById("room-name");
const roomDescEl = document.getElementById("room-desc");
const coordsEl = document.getElementById("coords");
const playersListEl = document.getElementById("players-list");
const messagesEl = document.getElementById("messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const moveButtons = document.querySelectorAll(".movement button");

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
