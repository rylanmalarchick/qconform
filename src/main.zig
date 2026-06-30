const std = @import("std");

// pulse-level conformance checking

const Device = struct {
    timing_grid_ns: u32,
};

const Pulse = struct {
    duration_ns: u32,
};

const Program = struct {
    pulses: []const Pulse,
};

const Rule = enum {
    grid_multiple,
};

const Rejection = struct {
    rule: Rule,
    element: u32,
    value_ns: u32,
    limit_ns: u32,
};

const Sink = struct {
    list: *std.ArrayList(Rejection),
    gpa: std.mem.Allocator,

    fn add(self: Sink, r: Rejection) std.mem.Allocator.Error!void {
        try self.list.append(self.gpa, r);
    }
};

const RuleError = error{ OutOfMemory, InvalidDescriptor };
const RuleFn = *const fn (Program, Device, Sink) RuleError!void;

const rules = [_]RuleFn{&grid_multiple};

// duration: positive whole-number multiple of grid
fn grid_multiple(program: Program, device: Device, sink: Sink) RuleError!void {
    const grid = device.timing_grid_ns;
    // no modulo by a zero grid
    if (grid == 0) return error.InvalidDescriptor;
    for (program.pulses, 0..) |pulse, i| {
        const d = pulse.duration_ns;
        if (d == 0 or d % grid != 0) {
            try sink.add(.{
                .rule = .grid_multiple,
                .element = @intCast(i),
                .value_ns = d,
                .limit_ns = grid,
            });
        }
    }
}

const Report = struct {
    rejections: []Rejection,

    fn conforms(self: Report) bool {
        return self.rejections.len == 0;
    }

    fn deinit(self: Report, gpa: std.mem.Allocator) void {
        gpa.free(self.rejections);
    }
};

fn check(gpa: std.mem.Allocator, program: Program, device: Device) RuleError!Report {
    var list: std.ArrayList(Rejection) = .empty;
    errdefer list.deinit(gpa);
    const sink = Sink{ .list = &list, .gpa = gpa };
    for (rules) |rule| {
        try rule(program, device, sink);
    }
    return .{ .rejections = try list.toOwnedSlice(gpa) };
}

pub fn main() void {
    std.debug.print("qconform: no input interface yet\n", .{});
}

test "on_grid" {
    const device = Device{ .timing_grid_ns = 4 };
    const program = Program{ .pulses = &.{.{ .duration_ns = 16 }} };
    const report = try check(std.testing.allocator, program, device);
    defer report.deinit(std.testing.allocator);
    try std.testing.expect(report.conforms());
}

test "off_grid" {
    const device = Device{ .timing_grid_ns = 4 };
    const program = Program{ .pulses = &.{.{ .duration_ns = 30 }} };
    const report = try check(std.testing.allocator, program, device);
    defer report.deinit(std.testing.allocator);
    try std.testing.expectEqual(@as(usize, 1), report.rejections.len);
    const r = report.rejections[0];
    try std.testing.expectEqual(Rule.grid_multiple, r.rule);
    try std.testing.expectEqual(@as(u32, 0), r.element);
    try std.testing.expectEqual(@as(u32, 30), r.value_ns);
    try std.testing.expectEqual(@as(u32, 4), r.limit_ns);
}

test "zero_duration" {
    const device = Device{ .timing_grid_ns = 4 };
    const program = Program{ .pulses = &.{.{ .duration_ns = 0 }} };
    const report = try check(std.testing.allocator, program, device);
    defer report.deinit(std.testing.allocator);
    try std.testing.expectEqual(@as(usize, 1), report.rejections.len);
    try std.testing.expectEqual(@as(u32, 0), report.rejections[0].value_ns);
}

test "zero_grid" {
    const device = Device{ .timing_grid_ns = 0 };
    const program = Program{ .pulses = &.{.{ .duration_ns = 16 }} };
    try std.testing.expectError(error.InvalidDescriptor, check(std.testing.allocator, program, device));
}
