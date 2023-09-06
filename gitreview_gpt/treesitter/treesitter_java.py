import tree_sitter
from gitreview_gpt.treesitter.treesitter import (
    Treesitter,
    TreesitterNode,
    get_source_from_node,
)
from gitreview_gpt.constants import Language
from gitreview_gpt.treesitter.treesitter_registry import TreesitterRegistry


class TreesitterJava(Treesitter):
    def __init__(self):
        super().__init__(Language.JAVA)

    def parse(self, file_bytes: bytes) -> list[TreesitterNode]:
        super().parse(file_bytes)
        result = []
        methods = self._query_all_methods(self.tree.root_node)
        for method in methods:
            method_name = self._query_method_name(method["method"])
            doc_comment = method["doc_comment"]
            result.append(TreesitterNode(method_name, doc_comment, method["method"]))
        return result

    def _query_method_name(self, node: tree_sitter.Node):
        if node.type == "method_declaration":
            for child in node.children:
                if child.type == "identifier":
                    return child.text.decode()
        return None

    def _query_all_methods(self, node: tree_sitter.Node):
        methods = []
        if node.type == "method_declaration":
            doc_comment_node = None
            if (
                node.prev_named_sibling
                and node.prev_named_sibling.type == "block_comment"
            ):
                doc_comment_node = get_source_from_node(node.prev_named_sibling)
            methods.append({"method": node, "doc_comment": doc_comment_node})
        else:
            for child in node.children:
                methods.extend(self._query_all_methods(child))
        return methods


# Register the TreesitterJava class in the registry
TreesitterRegistry.register_treesitter(Language.JAVA, TreesitterJava)