"""Diagnostic process attribution cannot silently compare different PG backends."""
import pytest

from scripts import qualification_load as load


def test_only_same_process_birth_and_positive_interval_produce_postgres_cpu():
    before = {(10, 100): 2.0, (11, 110): 1.0}
    after = {(10, 100): 3.5, (11, 110): 1.5}
    result = load.postgres_cpu_interval(before, after, 5)
    assert result == {"postgres_processes": 2, "postgres_cpu_interval_complete": True,
                      "postgres_cpu_percent_one_core": 40.0,
                      "postgres_cpu_lower_bound_percent_one_core": 40.0,
                      "postgres_processes_observed_both": 2, "postgres_processes_added": 0,
                      "postgres_processes_exited": 0}
    assert "10" not in str(result) and "110" not in str(result)


@pytest.mark.parametrize("before,after,elapsed", [
    (None, None, 5), ({}, {}, 5), (None, {(10, 100): 1.0}, 5),
    ({(10, 100): 2.0}, {(10, 101): 3.0}, 5),  # recycled PID
    ({(10, 100): 2.0}, {(10, 100): 1.0}, 5),  # counter regressed
    ({(10, 100): 2.0}, {(10, 100): 3.0, (11, 110): .5}, 5),
    ({(10, 100): 2.0, (11, 110): 1.0}, {(10, 100): 3.0}, 5),
    ({(10, 100): 2.0}, {(10, 100): 3.0}, 0),
])
def test_unavailable_partial_or_changed_process_sets_never_report_a_cpu_estimate(before, after, elapsed):
    result = load.postgres_cpu_interval(before, after, elapsed)
    assert result["postgres_cpu_interval_complete"] is False
    assert "postgres_cpu_percent_one_core" not in result


def test_process_collection_separates_api_children_from_postgres_without_command_arguments(monkeypatch):
    records = {10: (1, "python", 100, 20, 3), 11: (10, "python", 110, 30, 4),
               20: (1, "postgres", 200, 40, 5), 21: (20, "postgres", 210, 50, 6),
               22: (1, "psql", 220, 60, 7)}

    class Proc:
        def __init__(self, value):
            self.value = str(value)
            self.name = self.value.rsplit("/", 1)[-1]

        def is_dir(self):
            return True

        def glob(self, _):
            return [Proc("/proc/" + str(pid)) for pid in records]

        def __truediv__(self, value):
            return Proc(self.value + "/" + value)

        def read_text(self):
            assert self.name == "stat"  # Never cmdline, environment, or connection arguments.
            pid = int(self.value.split("/")[-2])
            parent, name, born, cpu, rss = records[pid]
            fields = ["0"] * 22
            fields[0], fields[1], fields[11], fields[19], fields[21] = "S", str(parent), str(cpu), str(born), str(rss)
            return str(pid) + " (" + name + ") " + " ".join(fields)

        def iterdir(self):
            assert self.name == "fd"
            return [1, 2]

    monkeypatch.setattr(load, "Path", Proc)
    monkeypatch.setattr(load.os, "sysconf", lambda key: 100 if key == "SC_CLK_TCK" else 4096, raising=False)
    result = load.process_metrics(10)
    assert result["processes"] == 2 and result["cpu_seconds"] == .5
    assert result["rss_bytes"] == 7 * 4096 and result["open_fds"] == 4
    assert result["_postgres_cpu_by_process"] == {(20, 200): .4, (21, 210): .5}


def test_churn_reports_only_a_lower_bound_of_surviving_identities_not_a_total():
    result = load.postgres_cpu_interval({(10, 100): 2., (11, 110): 1.},
                                        {(10, 100): 3., (11, 111): 4.}, 5)
    assert result["postgres_cpu_interval_complete"] is False
    assert "postgres_cpu_percent_one_core" not in result
    assert result["postgres_cpu_lower_bound_percent_one_core"] == 20.
    assert result["postgres_processes_observed_both"] == 1
    assert result["postgres_processes_added"] == result["postgres_processes_exited"] == 1
