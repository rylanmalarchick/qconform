# qconform: conformance checker for pulse programs against a device
# capability descriptor.
#
#   make            build ./qconform
#   make check      build, run unit tests, the golden corpus, and the tripwires
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

# Test binaries include the .c files under test directly, so they can reach
# file-private functions — the access the Zig `test` blocks had.
TESTS := test_rational test_json test_enums
TEST_BINS := $(addprefix $(SRC_DIR)/,$(TESTS))

.PHONY: all check test golden tripwires sanitize clean

all: $(BIN)

$(BIN): $(SOURCES) $(HEADERS)
	$(CC) $(CFLAGS) -o $@ $(SOURCES) $(LDFLAGS)

$(SRC_DIR)/test_rational: $(SRC_DIR)/test_rational.c $(SOURCES) $(HEADERS)
	$(CC) $(CFLAGS) -o $@ $<

$(SRC_DIR)/test_json: $(SRC_DIR)/test_json.c $(SOURCES) $(HEADERS)
	$(CC) $(CFLAGS) -o $@ $<

$(SRC_DIR)/test_enums: $(SRC_DIR)/test_enums.c $(SOURCES) $(HEADERS)
	$(CC) $(CFLAGS) -o $@ $< $(SRC_DIR)/rational.c

test: $(TEST_BINS)
	@for t in $(TEST_BINS); do printf '%s: ' "$$t"; ./$$t || exit 1; done

golden: $(BIN)
	@./tests/golden/run.sh ./$(BIN)

tripwires: $(BIN)
	@$(SRC_DIR)/tripwires.sh ./$(BIN)

check: test golden tripwires

# The arena is never freed per-allocation by design, and main frees it once at
# exit, so leak checking is meaningful here and is left on.
sanitize:
	$(MAKE) clean
	$(MAKE) check CFLAGS="-std=c99 -Wall -Wextra -Werror -O2 -g -fsanitize=undefined,address -fno-omit-frame-pointer"

clean:
	rm -f $(BIN) $(TEST_BINS)
