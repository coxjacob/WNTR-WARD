from ward.simulation import check_setup, load_test_network


def test_load_test_network():
    network = load_test_network()

    assert network.num_nodes == 11
    assert network.num_links == 13


def test_check_setup(capsys):
    check_setup()

    output = capsys.readouterr().out
    assert "WNTR version: 1.5.0" in output
    assert "Loaded Net1: 11 nodes, 13 links" in output
