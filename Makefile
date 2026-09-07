# qconform: conformance checker for pulse programs against a device
# capability descriptor.
#
#   make            build ./qconform
#   make check      build, run unit tests, the golden corpus, and the tripwires
#   make differential  qconform against the QICK toolchain (needs Python)
#   make sanitize   same, built with UBSan and ASan
#   make clean
#
# C99, no dependencies. 128-bit integers are required (GCC, Clang, or ICC);
# see src/intmath.h.

CC ?= cc
CFLAGS ?= -std=c99 -Wall -Wextra -Werror -O2
LDFLAGS ?=

SRC_DIR := src
BIN := qconform

SOURCES := \
	$(SRC_DIR)/main.c \
	$(SRC_DIR)/check.c \
	$(SRC_DIR)/report.c \
	$(SRC_DIR)/parse.c \
	$(SRC_DIR)/json.c \
	$(SRC_DIR)/enums.c \
	$(SRC_DIR)/rational.c \
	$(SRC_DIR)/arena.c

HEADERS := $(wildcard $(SRC_DIR)/*.h)

TEST_DIR := tests/unit

# Unit tests include the .c files under test directly, so they can reach
# file-private functions.
TESTS := test_rational test_json test_enums
TEST_BINS := $(addprefix $(TEST_DIR)/,$(TESTS))

SURVEY_PY ?= $(HOME)/.venvs/qconform-survey/bin/python
DIFF_OUT ?= /tmp/qconform-corpus

.PHONY: all check test golden tripwires sanitize differential clean

all: $(BIN)

$(BIN): $(SOURCES) $(HEADERS)
	$(CC) $(CFLAGS) -o $@ $(SOURCES) $(LDFLAGS)

$(TEST_DIR)/test_rational: $(TEST_DIR)/test_rational.c $(SOURCES) $(HEADERS)
	$(CC) $(CFLAGS) -o $@ $<

$(TEST_DIR)/test_json: $(TEST_DIR)/test_json.c $(SOURCES) $(HEADERS)
	$(CC) $(CFLAGS) -o $@ $<

$(TEST_DIR)/test_enums: $(TEST_DIR)/test_enums.c $(SOURCES) $(HEADERS)
	$(CC) $(CFLAGS) -o $@ $< $(SRC_DIR)/rational.c

test: $(TEST_BINS)
	@for t in $(TEST_BINS); do printf '%s: ' "$$t"; ./$$t || exit 1; done

golden: $(BIN)
	@./tests/golden/run.sh ./$(BIN)

tripwires: $(BIN)
	@tests/tripwires.sh ./$(BIN)

check: test golden tripwires

# The arena is never freed per-allocation by design, and main frees it once at
# exit, so leak checking is meaningful here and is left on.
sanitize:
	$(MAKE) clean
	$(MAKE) check CFLAGS="-std=c99 -Wall -Wextra -Werror -O2 -g -fsanitize=undefined,address -fno-omit-frame-pointer"

# The phase 5 evidence. Needs the survey environment, because it drives the
# vendor toolchain; see tools/differential/README.txt. Not part of `check`:
# it depends on a Python environment the checker itself does not need.
differential: $(BIN)
	@$(SURVEY_PY) tools/differential/check_lowering.py
	@for c in testbench qce2025-r26; do \
		echo "=== $$c ==="; \
		$(SURVEY_PY) tools/differential/corpus.py \
			tests/golden/descriptors/$$c.json $(DIFF_OUT)-$$c --seed 1 \
			--config tools/survey/configs/zcu216-$$c.json; \
		$(SURVEY_PY) tools/differential/run.py $(DIFF_OUT)-$$c \
			tests/golden/descriptors/$$c.json \
			tools/survey/configs/zcu216-$$c.json \
			tools/differential/results/$$c.jsonl; \
		$(SURVEY_PY) tools/differential/triage.py \
			tools/differential/results/$$c.jsonl \
			tests/golden/descriptors/$$c.json; \
	done

clean:
	rm -f $(BIN) $(TEST_BINS)
