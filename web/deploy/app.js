// ── State ──

let currentStep = 0;
const totalSteps = 6;
const tags = { roles: [], locations: [], exclude: [] };

// ── Navigation ──

function nextStep() {
  if (currentStep === 4) buildReview();
  if (currentStep < totalSteps) {
    document
      .querySelector(`.step[data-step="${currentStep}"]`)
      .classList.remove("active");
    currentStep++;
    const next = document.querySelector(`.step[data-step="${currentStep}"]`);
    next.classList.remove("active");
    void next.offsetWidth; // reflow for animation
    next.classList.add("active");
    updateProgress();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
}

function prevStep() {
  if (currentStep > 0) {
    document
      .querySelector(`.step[data-step="${currentStep}"]`)
      .classList.remove("active");
    currentStep--;
    const prev = document.querySelector(`.step[data-step="${currentStep}"]`);
    prev.classList.remove("active");
    void prev.offsetWidth;
    prev.classList.add("active");
    updateProgress();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
}

function updateProgress() {
  document.querySelectorAll(".progress-dot").forEach((dot) => {
    const step = parseInt(dot.dataset.step);
    dot.classList.toggle("active", step === currentStep);
    dot.classList.toggle("done", step < currentStep);
  });
  document.getElementById("stepCounter").textContent = Math.min(
    currentStep + 1,
    totalSteps,
  );
  document.body.classList.toggle("hero-mode", currentStep === 0);
}

// ── Tags ──

function handleTagKey(e, group) {
  if (e.key === "Enter" && e.target.value.trim()) {
    e.preventDefault();
    const val = e.target.value.trim();
    if (!tags[group].includes(val)) {
      tags[group].push(val);
      renderTags(group);
    }
    e.target.value = "";
  }
}

function renderTags(group) {
  const wrap = document.getElementById(`${group}Wrap`);
  const input = document.getElementById(`${group}Input`);
  wrap.querySelectorAll(".tag").forEach((t) => t.remove());
  tags[group].forEach((tag, i) => {
    const el = document.createElement("span");
    el.className = "tag";
    el.innerHTML = `${tag}<button onclick="removeTag('${group}',${i})">&times;</button>`;
    wrap.insertBefore(el, input);
  });
}

function removeTag(group, index) {
  tags[group].splice(index, 1);
  renderTags(group);
}

// ── File upload: resume ──

function handleFileUpload(input) {
  if (input.files.length) {
    const file = input.files[0];
    document.getElementById("uploadZone").classList.add("has-file");
    document.getElementById("uploadText").innerHTML =
      `<strong>${file.name}</strong><br><span class="file-types">${(file.size / 1024).toFixed(0)} KB &mdash; Click to change</span>`;

    if (file.name.endsWith(".txt") || file.name.endsWith(".md")) {
      const reader = new FileReader();
      reader.onload = (e) => {
        document.getElementById("resumeText").value = e.target.result;
      };
      reader.readAsText(file);
    }
  }
}

function initDragDrop(zoneId, fileInputId, handler) {
  const zone = document.getElementById(zoneId);
  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.style.borderColor = "var(--gold)";
  });
  zone.addEventListener("dragleave", () => {
    zone.style.borderColor = "";
  });
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.style.borderColor = "";
    if (e.dataTransfer.files.length) {
      document.getElementById(fileInputId).files = e.dataTransfer.files;
      handler(document.getElementById(fileInputId));
    }
  });
}

// ── File upload: cover letter ──

function handleCoverLetterUpload(input) {
  if (input.files.length) {
    const file = input.files[0];
    document.getElementById("clUploadZone").classList.add("has-file");
    document.getElementById("clUploadText").innerHTML =
      `<strong>${file.name}</strong><br><span class="file-types">${(file.size / 1024).toFixed(0)} KB &mdash; Click to change</span>`;

    if (file.name.endsWith(".txt") || file.name.endsWith(".md")) {
      const reader = new FileReader();
      reader.onload = (e) => {
        document.getElementById("coverLetterText").value = e.target.result;
      };
      reader.readAsText(file);
    }
  }
}

// ── Review builder ──

function buildReview() {
  const data = gatherData();
  let html = "";

  html += `<div class="review-section">
      <h3>Contact Information</h3>
      <div class="review-row"><span class="rlabel">Name</span><span class="rvalue">${data.firstName} ${data.lastName}</span></div>
      <div class="review-row"><span class="rlabel">Email</span><span class="rvalue">${data.email}</span></div>
      <div class="review-row"><span class="rlabel">Phone</span><span class="rvalue">${data.phone || "\u2014"}</span></div>
      <div class="review-row"><span class="rlabel">Location</span><span class="rvalue">${data.location}</span></div>
      <div class="review-row"><span class="rlabel">LinkedIn</span><span class="rvalue">${data.linkedin || "\u2014"}</span></div>
  </div>`;

  html += `<div class="review-section">
      <h3>Resume</h3>
      <div class="review-row"><span class="rlabel">Content</span><span class="rvalue">${data.resumeText ? data.resumeText.substring(0, 100) + "..." : "File uploaded"}</span></div>
      <div class="review-row"><span class="rlabel">Cover Letter</span><span class="rvalue">${data.coverLetterText ? data.coverLetterText.substring(0, 100) + "..." : "Not provided"}</span></div>
  </div>`;

  html += `<div class="review-section">
      <h3>Search Preferences</h3>
      <div class="review-row"><span class="rlabel">Target Roles</span><span class="rvalue"><div class="review-tags">${data.roles.map((r) => `<span class="review-tag">${r}</span>`).join("")}</div></span></div>
      <div class="review-row"><span class="rlabel">Locations</span><span class="rvalue"><div class="review-tags">${data.locations.map((l) => `<span class="review-tag">${l}</span>`).join("")}</div></span></div>
      <div class="review-row"><span class="rlabel">Min Salary</span><span class="rvalue">${data.minSalary || "No minimum"}</span></div>
      <div class="review-row"><span class="rlabel">Arrangement</span><span class="rvalue">${data.workArrangement}</span></div>
      ${data.exclude.length ? `<div class="review-row"><span class="rlabel">Excluded</span><span class="rvalue"><div class="review-tags">${data.exclude.map((e) => `<span class="review-tag">${e}</span>`).join("")}</div></span></div>` : ""}
  </div>`;

  html += `<div class="review-section">
      <h3>Experience Discovery</h3>
      ${data.discovery.teamSize ? `<div class="review-row"><span class="rlabel">Team Size</span><span class="rvalue">${data.discovery.teamSize}</span></div>` : ""}
      ${data.discovery.budget ? `<div class="review-row"><span class="rlabel">Largest Budget</span><span class="rvalue">${data.discovery.budget}</span></div>` : ""}
      ${data.discovery.certs ? `<div class="review-row"><span class="rlabel">Certifications</span><span class="rvalue">${data.discovery.certs}</span></div>` : ""}
      ${data.discovery.tools ? `<div class="review-row"><span class="rlabel">Tools</span><span class="rvalue">${data.discovery.tools}</span></div>` : ""}
      ${data.discovery.industries ? `<div class="review-row"><span class="rlabel">Industries</span><span class="rvalue">${data.discovery.industries}</span></div>` : ""}
  </div>`;

  document.getElementById("reviewContent").innerHTML = html;
}

// ── Data gathering ──

function gatherData() {
  return {
    firstName: document.getElementById("firstName").value.trim(),
    lastName: document.getElementById("lastName").value.trim(),
    email: document.getElementById("email").value.trim(),
    phone: document.getElementById("phone").value.trim(),
    location: document.getElementById("location").value.trim(),
    linkedin: document.getElementById("linkedin").value.trim(),
    resumeText: document.getElementById("resumeText").value.trim(),
    resumeFile: document.getElementById("resumeFile").files[0]?.name || "",
    coverLetterText: document.getElementById("coverLetterText").value.trim(),
    roles: tags.roles,
    locations: tags.locations,
    exclude: tags.exclude,
    minSalary: document.getElementById("minSalary").value.trim(),
    workArrangement: document.getElementById("workArrangement").value,
    prefNotes: document.getElementById("prefNotes").value.trim(),
    discovery: {
      teamSize: document.getElementById("d_teamSize").value.trim(),
      budget: document.getElementById("d_budget").value.trim(),
      metrics: document.getElementById("d_metrics").value.trim(),
      certs: document.getElementById("d_certs").value.trim(),
      challenge: document.getElementById("d_challenge").value.trim(),
      tools: document.getElementById("d_tools").value.trim(),
      industries: document.getElementById("d_industries").value.trim(),
      hidden: document.getElementById("d_hidden").value.trim(),
    },
    submittedAt: new Date().toISOString(),
  };
}

// ── Apps Script endpoint ──
// Replace this URL after deploying your Google Apps Script web app
const APPS_SCRIPT_URL =
  "https://script.google.com/macros/s/AKfycbz2m150WPtCbYW40GJ_2Y8kE16gxN0ezfQ7W6n-EJGdpiT7fXIYxt63ogNdFm6v11BY/exec";

// ── Submit ──

function submitOnboarding() {
  const data = gatherData();
  const btn = document.getElementById("submitBtn");
  btn.disabled = true;
  btn.textContent = "Submitting...";

  fetch(APPS_SCRIPT_URL, {
    method: "POST",
    mode: "no-cors",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })
    .then(() => {
      nextStep();
    })
    .catch((err) => {
      console.error("Submission error:", err);
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download =
        `onboarding_${data.firstName}_${data.lastName}_${Date.now()}.json`.replace(
          /\s+/g,
          "_",
        );
      a.click();
      URL.revokeObjectURL(url);
      nextStep();
    });
}

// ── Init drag & drop on load ──

document.addEventListener("DOMContentLoaded", () => {
  initDragDrop("uploadZone", "resumeFile", handleFileUpload);
  initDragDrop("clUploadZone", "coverLetterFile", handleCoverLetterUpload);
});
