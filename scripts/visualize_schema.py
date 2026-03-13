"""Visualize OMOP CDM schema as a graph.

This script creates a visual representation of the OMOP CDM schema
defined in src/fastssv/schemas/cdm_schema.py.

Useful for:
- Understanding table relationships
- Debugging join path validation
- Documentation
- Identifying schema patterns

Usage:
    python scripts/visualize_schema.py
    python scripts/visualize_schema.py --output schema.png
    python scripts/visualize_schema.py --format svg
    python scripts/visualize_schema.py --focus condition_occurrence

Requirements:
    pip install networkx matplotlib pygraphviz
"""

import argparse
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import networkx as nx
import matplotlib.pyplot as plt
from fastssv.schemas.cdm_schema import CDM_SCHEMA


class OMOPSchemaGraph:
    """OMOP CDM schema represented as a directed graph."""

    def __init__(self):
        self.G = nx.DiGraph()
        self._load_schema()

    def _load_schema(self):
        """Load CDM_SCHEMA into NetworkX graph."""
        for table, info in CDM_SCHEMA.items():
            # Add node for each table
            self.G.add_node(
                table,
                primary_key=info["primary_key"],
                node_type=self._classify_table(table)
            )

            # Add edges for foreign key relationships
            for target_table, (fk, pk) in info["edges"].items():
                # Some edges point to the same table with different aliases
                # Normalize to actual table name
                actual_target = target_table.split("_")[0] if "_" in target_table else target_table

                # Check if target exists in schema (some edges may be to aliased versions)
                if actual_target not in CDM_SCHEMA and target_table in CDM_SCHEMA:
                    actual_target = target_table

                self.G.add_edge(
                    table,
                    actual_target,
                    fk=fk,
                    pk=pk,
                    label=f"{fk}→{pk}"
                )

    def _classify_table(self, table: str) -> str:
        """Classify table into categories for coloring."""
        clinical_tables = {
            "condition_occurrence", "drug_exposure", "procedure_occurrence",
            "measurement", "observation", "visit_occurrence", "visit_detail",
            "device_exposure", "note", "specimen", "death"
        }

        vocab_tables = {
            "concept", "concept_ancestor", "concept_relationship",
            "concept_synonym", "vocabulary", "domain", "concept_class",
            "relationship", "drug_strength"
        }

        if table in clinical_tables:
            return "clinical"
        elif table in vocab_tables:
            return "vocabulary"
        elif table == "person":
            return "person"
        else:
            return "other"

    def find_path(self, source: str, target: str, max_length: int = 3):
        """Find all paths between two tables."""
        try:
            paths = list(nx.all_simple_paths(self.G, source, target, cutoff=max_length))
            return paths
        except nx.NetworkXNoPath:
            return []

    def get_neighbors(self, table: str):
        """Get all tables that can be joined to this table."""
        return {
            "outgoing": list(self.G.successors(table)),
            "incoming": list(self.G.predecessors(table))
        }

    def visualize(
        self,
        output_file: str = "omop_schema.png",
        format: str = "png",
        focus_table: str = None,
        max_depth: int = 2
    ):
        """Visualize the schema graph."""
        # If focusing on a specific table, create subgraph
        if focus_table:
            # Get all nodes within max_depth
            nodes = {focus_table}
            for _ in range(max_depth):
                new_nodes = set()
                for node in nodes:
                    new_nodes.update(self.G.successors(node))
                    new_nodes.update(self.G.predecessors(node))
                nodes.update(new_nodes)

            G_vis = self.G.subgraph(nodes).copy()
        else:
            G_vis = self.G

        # Create layout
        try:
            # Try graphviz layout (best for directed graphs)
            pos = nx.nx_agraph.graphviz_layout(G_vis, prog='dot')
        except:
            # Fallback to spring layout
            pos = nx.spring_layout(G_vis, k=2, iterations=50)

        # Set up colors
        color_map = {
            "clinical": "#FF6B6B",
            "vocabulary": "#4ECDC4",
            "person": "#95E1D3",
            "other": "#F8B195"
        }

        node_colors = [
            color_map.get(G_vis.nodes[node].get("node_type", "other"), "#CCCCCC")
            for node in G_vis.nodes()
        ]

        # Create figure
        plt.figure(figsize=(20, 12))

        # Draw nodes
        nx.draw_networkx_nodes(
            G_vis, pos,
            node_color=node_colors,
            node_size=3000,
            alpha=0.9
        )

        # Draw edges
        nx.draw_networkx_edges(
            G_vis, pos,
            edge_color='gray',
            arrows=True,
            arrowsize=20,
            width=2,
            alpha=0.6,
            connectionstyle="arc3,rad=0.1"
        )

        # Draw labels
        nx.draw_networkx_labels(
            G_vis, pos,
            font_size=8,
            font_weight='bold'
        )

        # Draw edge labels (FK relationships)
        edge_labels = nx.get_edge_attributes(G_vis, 'label')
        nx.draw_networkx_edge_labels(
            G_vis, pos,
            edge_labels,
            font_size=6,
            alpha=0.7
        )

        # Add legend
        from matplotlib.patches import Patch
        legend_elements = [
            Patch(facecolor='#FF6B6B', label='Clinical Tables'),
            Patch(facecolor='#4ECDC4', label='Vocabulary Tables'),
            Patch(facecolor='#95E1D3', label='Person Table'),
            Patch(facecolor='#F8B195', label='Other Tables'),
        ]
        plt.legend(handles=legend_elements, loc='upper left', fontsize=10)

        plt.title(
            f"OMOP CDM v5.4 Schema Graph{' - ' + focus_table if focus_table else ''}",
            fontsize=16,
            fontweight='bold'
        )
        plt.axis('off')
        plt.tight_layout()

        # Save
        output_path = Path(output_file)
        plt.savefig(output_path, format=format, dpi=300, bbox_inches='tight')
        print(f"Schema visualization saved to: {output_path}")

        return output_path

    def analyze_connectivity(self):
        """Analyze schema connectivity."""
        print("OMOP CDM Schema Analysis")
        print("=" * 60)
        print(f"Total tables: {self.G.number_of_nodes()}")
        print(f"Total relationships: {self.G.number_of_edges()}")
        print()

        # Most connected tables
        in_degrees = dict(self.G.in_degree())
        out_degrees = dict(self.G.out_degree())

        print("Top 10 most referenced tables (incoming edges):")
        for table, degree in sorted(in_degrees.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"  {table:30} {degree:3} incoming edges")
        print()

        print("Top 10 tables with most foreign keys (outgoing edges):")
        for table, degree in sorted(out_degrees.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"  {table:30} {degree:3} outgoing edges")
        print()

        # Check if graph is strongly connected
        if nx.is_strongly_connected(self.G):
            print("Schema is strongly connected (all tables reachable from all tables)")
        else:
            print("Schema is NOT strongly connected")
            # Find strongly connected components
            sccs = list(nx.strongly_connected_components(self.G))
            print(f"  {len(sccs)} strongly connected components")

        # Check if graph is weakly connected
        if nx.is_weakly_connected(self.G):
            print("Schema is weakly connected (all tables connected ignoring direction)")
        else:
            print("Schema is NOT weakly connected")

    def export_to_graphml(self, output_file: str = "omop_schema.graphml"):
        """Export schema to GraphML format for use in other tools."""
        nx.write_graphml(self.G, output_file)
        print(f"Schema exported to GraphML: {output_file}")

    def export_to_dot(self, output_file: str = "omop_schema.dot"):
        """Export schema to DOT format for Graphviz."""
        nx.nx_agraph.write_dot(self.G, output_file)
        print(f"Schema exported to DOT: {output_file}")


def main():
    parser = argparse.ArgumentParser(description="Visualize OMOP CDM schema")
    parser.add_argument(
        "--output", "-o",
        default="omop_schema.png",
        help="Output file path (default: omop_schema.png)"
    )
    parser.add_argument(
        "--format", "-f",
        default="png",
        choices=["png", "svg", "pdf", "jpg"],
        help="Output format (default: png)"
    )
    parser.add_argument(
        "--focus",
        help="Focus on a specific table and its neighbors"
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=2,
        help="Depth of neighborhood when using --focus (default: 2)"
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Print schema connectivity analysis"
    )
    parser.add_argument(
        "--export-graphml",
        help="Export to GraphML format"
    )
    parser.add_argument(
        "--export-dot",
        help="Export to DOT format"
    )
    parser.add_argument(
        "--find-path",
        nargs=2,
        metavar=("SOURCE", "TARGET"),
        help="Find all paths between two tables"
    )

    args = parser.parse_args()

    # Create schema graph
    schema = OMOPSchemaGraph()

    # Handle different commands
    if args.analyze:
        schema.analyze_connectivity()

    if args.find_path:
        source, target = args.find_path
        paths = schema.find_path(source, target)
        print(f"\nPaths from {source} to {target}:")
        if paths:
            for i, path in enumerate(paths, 1):
                print(f"  Path {i}: {' -> '.join(path)}")
        else:
            print(f"  No path found")

    if args.export_graphml:
        schema.export_to_graphml(args.export_graphml)

    if args.export_dot:
        schema.export_to_dot(args.export_dot)

    # Always create visualization unless only doing analysis/export
    if not (args.analyze and not args.output):
        schema.visualize(
            output_file=args.output,
            format=args.format,
            focus_table=args.focus,
            max_depth=args.depth
        )


if __name__ == "__main__":
    main()
