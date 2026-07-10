document.addEventListener("DOMContentLoaded", function () {
	const inputs = document.querySelectorAll(
		'input:not([placeholder])'
		+ ':not([type="hidden"])'
		+ ':not([type="checkbox"])'
		+ ':not([type="radio"])'
		+ ':not([type="submit"])'
		+ ':not([type="button"])'
		+ ':not([readonly])'
	);

	const cleanText = function (text) {
		return text.replace(/\s+/g, " ").replace(/:\s*$/, "").trim();
	};

	const fallbackByType = function (input) {
		const name = (input.name || "").replace(/\[\]$/, "").replace(/_/g, " ").trim();
		const lower = name.toLowerCase();

		if (input.type === "number") {
			if (/amount|value|total|balance|cost|income|expense/.test(lower)) {
				return "Amount (KSh)";
			}
			if (/count|number|no/.test(lower)) {
				return "Enter number";
			}
			return "Enter number";
		}

		if (input.type === "email") {
			return "name@example.com";
		}

		if (input.type === "date") {
			return "MM/DD/YYYY";
		}

		if (name) {
			return name.replace(/\b\w/g, function (char) {
				return char.toUpperCase();
			});
		}

		return "Enter value";
	};

	inputs.forEach(function (input) {
		let placeholder = "";

		if (input.id) {
			const safeId = (typeof CSS !== "undefined" && CSS.escape) ? CSS.escape(input.id) : input.id;
			const label = document.querySelector('label[for="' + safeId + '"]');
			if (label) {
				placeholder = cleanText(label.textContent || "");
			}
		}

		if (!placeholder) {
			const prev = input.previousElementSibling;
			if (prev && prev.tagName === "LABEL") {
				placeholder = cleanText(prev.textContent || "");
			}
		}

		if (!placeholder) {
			const parentLabel = input.closest("label");
			if (parentLabel) {
				placeholder = cleanText(parentLabel.textContent || "");
			}
		}

		if (!placeholder) {
			const parent = input.parentElement;
			if (parent) {
				const label = parent.querySelector("label");
				if (label) {
					placeholder = cleanText(label.textContent || "");
				}
			}
		}

		if (!placeholder) {
			placeholder = fallbackByType(input);
		}

		if (placeholder) {
			input.setAttribute("placeholder", placeholder);
		}
	});
});

document.addEventListener("DOMContentLoaded", function () {
	const form = document.getElementById("pbo-report-form");
	if (!form) {
		return;
	}
	if (window.useForm14InlineDraftManager === true) {
		return;
	}

	const submissionTokenInput = form.querySelector('input[name="submission_token"]');
	const submissionToken = submissionTokenInput ? (submissionTokenInput.value || "").trim() : "";
	const storageKey = "form14Draft:" + window.location.pathname;
	let saveTimer = null;
	const escapeNameForSelector = function (value) {
		if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
			return CSS.escape(value);
		}
		return String(value).replace(/(["\\\]])/g, "\\$1");
	};

	const shouldTrackField = function (field) {
		if (!field || !field.name || field.disabled) {
			return false;
		}
		if (field.matches('[type="hidden"], [type="submit"], [type="button"], [type="file"], [type="password"]')) {
			return false;
		}
		if (field.hasAttribute("data-generated")) {
			return false;
		}
		return true;
	};

	const getTrackedFields = function () {
		return Array.from(form.querySelectorAll("input, select, textarea")).filter(shouldTrackField);
	};

	const getFieldKey = function (field) {
		const selector = '[name="' + escapeNameForSelector(field.name) + '"]';
		const matchingFields = Array.from(form.querySelectorAll(selector)).filter(shouldTrackField);
		return field.name + "::" + matchingFields.indexOf(field);
	};

	const serializeDraft = function () {
		const fields = {};
		getTrackedFields().forEach(function (field) {
			const key = getFieldKey(field);
			if (field.type === "checkbox" || field.type === "radio") {
				fields[key] = field.checked;
				return;
			}
			if (field.tagName === "SELECT" && field.multiple) {
				fields[key] = Array.from(field.selectedOptions).map(function (option) {
					return option.value;
				});
				return;
			}
			fields[key] = field.value;
		});
		return {
			submissionToken: submissionToken,
			savedAt: Date.now(),
			fields: fields
		};
	};

	const saveDraft = function () {
		try {
			window.localStorage.setItem(storageKey, JSON.stringify(serializeDraft()));
		} catch (error) {
			// Ignore storage quota/private mode failures so form use is unaffected.
		}
	};

	const scheduleSaveDraft = function () {
		window.clearTimeout(saveTimer);
		saveTimer = window.setTimeout(saveDraft, 250);
	};

	const clearDraft = function () {
		try {
			window.localStorage.removeItem(storageKey);
		} catch (error) {
			// Ignore storage failures.
		}
	};

	const restoreDraft = function () {
		let savedDraft = null;
		try {
			savedDraft = JSON.parse(window.localStorage.getItem(storageKey) || "null");
		} catch (error) {
			clearDraft();
			return;
		}

		if (!savedDraft || !savedDraft.fields) {
			return;
		}

		if ((savedDraft.submissionToken || "") !== submissionToken) {
			clearDraft();
			return;
		}

		getTrackedFields().forEach(function (field) {
			const key = getFieldKey(field);
			if (!(key in savedDraft.fields)) {
				return;
			}

			const savedValue = savedDraft.fields[key];
			if (field.type === "checkbox" || field.type === "radio") {
				field.checked = Boolean(savedValue);
			} else if (field.tagName === "SELECT" && field.multiple && Array.isArray(savedValue)) {
				Array.from(field.options).forEach(function (option) {
					option.selected = savedValue.includes(option.value);
				});
			} else if (typeof savedValue === "string") {
				field.value = savedValue;
			}

			field.dispatchEvent(new Event("change", { bubbles: true }));
			field.dispatchEvent(new Event("input", { bubbles: true }));
		});
	};

	restoreDraft();

	form.addEventListener("input", scheduleSaveDraft);
	form.addEventListener("change", scheduleSaveDraft);
	form.addEventListener("submit", function () {
		window.clearTimeout(saveTimer);
		saveDraft();
	});
	window.addEventListener("beforeunload", function () {
		window.clearTimeout(saveTimer);
		saveDraft();
	});
	window.addEventListener("pageshow", function (event) {
		if (event.persisted) {
			restoreDraft();
		}
	});
	window.clearForm14Draft = clearDraft;
});

document.addEventListener("DOMContentLoaded", function () {
	if (window.location.pathname === "/login") {
		return;
	}

	const uppercaseSelector = [
		'input[type="text"]',
		'input[type="email"]',
		'input[type="search"]',
		'input[type="url"]',
		'input[type="tel"]',
		'textarea'
	].join(", ");
	const uppercaseExclusions = new Set(["signature"]);

	document.querySelectorAll(uppercaseSelector).forEach(function (field) {
		const fieldName = (field.name || "").toLowerCase();
		const fieldId = (field.id || "").toLowerCase();
		if (field.readOnly || field.disabled) {
			return;
		}
		if (uppercaseExclusions.has(fieldName) || uppercaseExclusions.has(fieldId)) {
			return;
		}

		field.addEventListener("input", function () {
			const start = field.selectionStart;
			const end = field.selectionEnd;
			const nextValue = field.value.toUpperCase();
			if (field.value !== nextValue) {
				field.value = nextValue;
				if (typeof start === "number" && typeof end === "number") {
					field.setSelectionRange(start, end);
				}
			}
		});
	});
});

const PHONE_COUNTRY_CODE_ADDITIONS = [
	{ name: "American Samoa", code: "+1-684" },
	{ name: "Anguilla", code: "+1-264" },
	{ name: "Aruba", code: "+297" },
	{ name: "Bermuda", code: "+1-441" },
	{ name: "British Virgin Islands", code: "+1-284" },
	{ name: "Cayman Islands", code: "+1-345" },
	{ name: "Cook Islands", code: "+682" },
	{ name: "Curacao", code: "+599" },
	{ name: "Faroe Islands", code: "+298" },
	{ name: "Falkland Islands", code: "+500" },
	{ name: "French Guiana", code: "+594" },
	{ name: "French Polynesia", code: "+689" },
	{ name: "Gibraltar", code: "+350" },
	{ name: "Greenland", code: "+299" },
	{ name: "Guadeloupe", code: "+590" },
	{ name: "Guam", code: "+1-671" },
	{ name: "Guernsey", code: "+44-1481" },
	{ name: "Isle of Man", code: "+44-1624" },
	{ name: "Ivory Coast (Cote d'Ivoire)", code: "+225" },
	{ name: "Jersey", code: "+44-1534" },
	{ name: "Kosovo", code: "+383" },
	{ name: "Martinique", code: "+596" },
	{ name: "Mayotte", code: "+262" },
	{ name: "Montserrat", code: "+1-664" },
	{ name: "New Caledonia", code: "+687" },
	{ name: "Niue", code: "+683" },
	{ name: "North Korea", code: "+850" },
	{ name: "Northern Mariana Islands", code: "+1-670" },
	{ name: "Puerto Rico", code: "+1-787" },
	{ name: "Reunion", code: "+262" },
	{ name: "Saint Barthelemy", code: "+590" },
	{ name: "Saint Helena", code: "+290" },
	{ name: "Saint Martin", code: "+590" },
	{ name: "Saint Pierre and Miquelon", code: "+508" },
	{ name: "Sint Maarten", code: "+1-721" },
	{ name: "Tokelau", code: "+690" },
	{ name: "Turks and Caicos Islands", code: "+1-649" },
	{ name: "U.S. Virgin Islands", code: "+1-340" },
	{ name: "Vatican City", code: "+379" },
	{ name: "Wallis and Futuna", code: "+681" },
	{ name: "Western Sahara", code: "+212" }
];

function normalizePhoneCountryOptionLabel(text) {
	return String(text || "")
		.replace(/\+\S+\s*$/, "")
		.replace(/\s+/g, " ")
		.trim()
		.toUpperCase();
}

function appendMissingPhoneCountryCodeOptions() {
	const selects = [];
	document.querySelectorAll('select[name="contact_country_code"]').forEach(function (select) {
		selects.push(select);
	});
	["telephone_country_code", "cellphone_country_code"].forEach(function (id) {
		const select = document.getElementById(id);
		if (select) {
			selects.push(select);
		}
	});

	selects.forEach(function (select) {
		const existingLabels = new Set(
			Array.from(select.options).map(function (option) {
				return normalizePhoneCountryOptionLabel(option.textContent);
			})
		);

		PHONE_COUNTRY_CODE_ADDITIONS.forEach(function (entry) {
			const normalizedLabel = normalizePhoneCountryOptionLabel(entry.name);
			if (existingLabels.has(normalizedLabel)) {
				return;
			}

			const option = document.createElement("option");
			option.value = entry.code;
			option.textContent = entry.name + " " + entry.code;

			const insertBefore = Array.from(select.options).find(function (existingOption) {
				const existingLabel = normalizePhoneCountryOptionLabel(existingOption.textContent);
				if (!existingLabel) {
					return false;
				}
				return existingLabel.localeCompare(normalizedLabel, "en", { sensitivity: "base" }) > 0;
			});

			if (insertBefore) {
				select.insertBefore(option, insertBefore);
			} else {
				select.appendChild(option);
			}
			existingLabels.add(normalizedLabel);
		});
	});
}

document.addEventListener("DOMContentLoaded", appendMissingPhoneCountryCodeOptions);

const COUNTRY_OPTION_ADDITIONS = [
	"American Samoa",
	"Aruba",
	"Bermuda",
	"British Virgin Islands",
	"Cayman Islands",
	"Channel Islands",
	"Cook Islands",
	"Cote d'Ivoire (Ivory Coast)",
	"Curacao",
	"Faroe Islands",
	"French Polynesia",
	"Gibraltar",
	"Greenland",
	"Guam",
	"Hong Kong SAR, China",
	"Isle of Man",
	"Kosovo",
	"Macao SAR, China",
	"New Caledonia",
	"Niue",
	"Northern Mariana Islands",
	"Puerto Rico (US)",
	"Sint Maarten (Dutch part)",
	"St. Martin (French part)",
	"Turks and Caicos Islands",
	"Virgin Islands (U.S.)",
	"West Bank and Gaza",
	"Anguilla",
	"Falkland Islands",
	"French Guiana",
	"Guadeloupe",
	"Guernsey",
	"Ivory Coast (Cote d'Ivoire)",
	"Jersey",
	"Martinique",
	"Mayotte",
	"Montserrat",
	"Puerto Rico",
	"Reunion",
	"Saint Barthelemy",
	"Saint Helena",
	"Saint Martin",
	"Saint Pierre and Miquelon",
	"Sint Maarten",
	"Tokelau",
	"U.S. Virgin Islands",
	"Vatican City",
	"Wallis and Futuna",
	"Western Sahara"
];

function normalizeCountryOptionLabel(text) {
	return String(text || "")
		.replace(/\s+/g, " ")
		.trim()
		.toUpperCase();
}

function appendMissingCountryOptions() {
	const selectors = [
		'select[name="contact_nationality"]',
		'select[name="donor_country[]"]',
		'select[name="grant_country[]"]',
		'select[name="official_nationality[]"]'
	];

	document.querySelectorAll(selectors.join(", ")).forEach(function (select) {
		const existingLabels = new Set(
			Array.from(select.options).map(function (option) {
				return normalizeCountryOptionLabel(option.textContent);
			})
		);

		COUNTRY_OPTION_ADDITIONS.forEach(function (country) {
			const normalizedLabel = normalizeCountryOptionLabel(country);
			if (existingLabels.has(normalizedLabel)) {
				return;
			}

			const option = document.createElement("option");
			option.value = country;
			option.textContent = country;

			const insertBefore = Array.from(select.options).find(function (existingOption) {
				const existingLabel = normalizeCountryOptionLabel(existingOption.textContent);
				if (!existingLabel || existingLabel.startsWith("-- ") || existingLabel === "ALL COUNTRIES" || existingLabel === "OTHER COUNTRY (SPECIFY)") {
					return false;
				}
				return existingLabel.localeCompare(normalizedLabel, "en", { sensitivity: "base" }) > 0;
			});

			if (insertBefore) {
				select.insertBefore(option, insertBefore);
			} else {
				select.appendChild(option);
			}
			existingLabels.add(normalizedLabel);
		});
	});
}

document.addEventListener("DOMContentLoaded", appendMissingCountryOptions);

function normalizeKenyanPhoneValue(rawValue, countryCode) {
	const value = (rawValue || "").replace(/[^\d+]/g, "");
	const digitsOnly = value.replace(/\D/g, "");
	const isKenyanInternational = countryCode === "+254" || value.startsWith("+254") || digitsOnly.startsWith("254");
	const isValidKenyanPrefix = function (digits) {
		return digits.startsWith("7") || digits.startsWith("1");
	};

	if (isKenyanInternational) {
		let localDigits = digitsOnly;
		if (localDigits.startsWith("254")) {
			localDigits = localDigits.slice(3);
		}
		if (localDigits.startsWith("0")) {
			localDigits = localDigits.slice(1);
		}
		localDigits = localDigits.slice(0, 9);
		return {
			value: countryCode === "+254" ? localDigits : "+254" + localDigits,
			valid: localDigits.length === 9 && isValidKenyanPrefix(localDigits),
			message: ""
		};
	}

	if (digitsOnly.startsWith("07") || digitsOnly.startsWith("01")) {
		const localDigits = digitsOnly.slice(0, 10);
		return {
			value: localDigits,
			valid: localDigits.length === 10,
			message: ""
		};
	}

	return null;
}

function enforcePhoneField(input, countryCode) {
	const normalizedKenyan = normalizeKenyanPhoneValue(input.value, countryCode);
	if (normalizedKenyan) {
		input.value = normalizedKenyan.value;
		return normalizedKenyan;
	}

	if (countryCode) {
		input.value = input.value.replace(/\D/g, "");
		return { value: input.value, valid: input.value.length > 0, message: "" };
	}

	input.value = input.value.replace(/[^\d+]/g, "");
	return { value: input.value, valid: input.value.length > 0, message: "" };
}

window.attachPhoneFieldValidation = function (config) {
	const inputs = Array.from(document.querySelectorAll(config.inputSelector || ""));
	if (!inputs.length) {
		return;
	}

	const selects = config.selectSelector ? Array.from(document.querySelectorAll(config.selectSelector)) : [];
	const errors = config.errorSelector ? Array.from(document.querySelectorAll(config.errorSelector)) : [];

	inputs.forEach(function (input, index) {
		if (!input || input.dataset.phoneValidationAttached === "1") {
			return;
		}
		input.dataset.phoneValidationAttached = "1";

		const select = selects[index] || null;
		const error = errors[index] || null;

		const sync = function () {
			if (input.dataset.phoneFreeform === "1") {
				input.value = input.value.replace(/[^\d+\-\s()]/g, "");
				input.removeAttribute("maxlength");
				if (error) {
					error.textContent = "";
				}
				input.setCustomValidity("");
				return;
			}

			const countryCode = select ? select.value : "";
			const result = enforcePhoneField(input, countryCode);
			if (countryCode === "+254") {
				input.maxLength = 9;
			} else if (!select && input.value.startsWith("+254")) {
				input.maxLength = 13;
			} else if (!select && /^(07|01)/.test(input.value.replace(/\D/g, ""))) {
				input.maxLength = 10;
			} else {
				input.removeAttribute("maxlength");
			}
			if (error) {
				error.textContent = result.valid || !input.value ? "" : result.message;
			}
			if (result.message) {
				input.setCustomValidity(result.valid || !input.value ? "" : result.message);
			} else {
				input.setCustomValidity("");
			}
		};

		input.addEventListener("input", sync);
		input.addEventListener("blur", sync);
		if (select) {
			select.addEventListener("change", sync);
		}
		sync();
	});
};

document.addEventListener("DOMContentLoaded", function () {
	window.attachPhoneFieldValidation({
		inputSelector: 'input[name="contact_telephone"], input[name="contact_telephone[]"]',
		selectSelector: 'select[name="contact_country_code"]',
		errorSelector: "#phone-error"
	});
	window.attachPhoneFieldValidation({
		inputSelector: 'input[name="telephone"]',
		selectSelector: "#telephone_country_code",
		errorSelector: "#phone-error1"
	});
	window.attachPhoneFieldValidation({
		inputSelector: 'input[name="cell_phone"]',
		selectSelector: "#cellphone_country_code",
		errorSelector: "#phone-error2"
	});
	window.attachPhoneFieldValidation({
		inputSelector: 'input[name="official_phone[]"]'
	});
});

document.addEventListener("DOMContentLoaded", function () {
	document.querySelectorAll('input[name="pbo_name"]').forEach(function (input) {
		if (input.dataset.uniquePboNameAttached === "1") {
			return;
		}
		input.dataset.uniquePboNameAttached = "1";
		input.addEventListener("input", function () {
			input.setCustomValidity("");
		});
	});
});





// REPORTING PERIOD
document.addEventListener("DOMContentLoaded", function () {
	const reportingPeriodStart = document.getElementById("reporting_period_start");
	const reportingPeriodEnd = document.getElementById("reporting_period_end");
	const reportingPeriodEndHidden = document.getElementById("reporting_period_end_hidden");

	if (!reportingPeriodStart || !reportingPeriodEnd) {
		return;
	}

	const parseReportingDate = function (value) {
		if (!value) {
			return null;
		}
		const normalized = value.trim();
		if (/^\d{2}\/\d{2}\/\d{4}$/.test(normalized)) {
			const parts = normalized.split("/").map(Number);
			return new Date(parts[2], parts[1] - 1, parts[0]);
		}
		if (/^\d{4}-\d{2}-\d{2}$/.test(normalized)) {
			const parts = normalized.split("-").map(Number);
			return new Date(parts[0], parts[1] - 1, parts[2]);
		}
		return null;
	};

	const formatReportingDate = function (dateValue) {
		const day = String(dateValue.getDate()).padStart(2, "0");
		const month = String(dateValue.getMonth() + 1).padStart(2, "0");
		const year = dateValue.getFullYear();
		return day + "/" + month + "/" + year;
	};

	reportingPeriodStart.addEventListener("change", function () {
		const startDate = parseReportingDate(reportingPeriodStart.value);
		if (!startDate || Number.isNaN(startDate.getTime())) {
			reportingPeriodEnd.value = "";
			if (reportingPeriodEndHidden) {
				reportingPeriodEndHidden.value = "";
			}
			return;
		}

		const targetMonthStart = new Date(startDate.getFullYear(), startDate.getMonth() + 11, 1);
		const targetYear = targetMonthStart.getFullYear();
		const targetMonth = targetMonthStart.getMonth();
		let targetDay = new Date(targetYear, targetMonth + 1, 0).getDate();

		if (targetMonth === 1) {
			targetDay = 28;
		}

		const formattedEndDate = formatReportingDate(new Date(targetYear, targetMonth, targetDay));
		reportingPeriodEnd.value = formattedEndDate;
		if (reportingPeriodEndHidden) {
			reportingPeriodEndHidden.value = formattedEndDate;
		}
	});
});
