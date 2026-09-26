import QtQuick 2.15

Column {
    id: root
    spacing: 16

    Text {
        width: parent.width
        text: "Remove '" + (backend.panelData.appName || "this app") + "' from LinuxTV?"
        color: theme.text
        font.family: theme.font_family
        font.pixelSize: 15
        wrapMode: Text.WordWrap
    }

    Row {
        spacing: 10
        anchors.right: parent.right

        Rectangle {
            width: 90
            height: 38
            radius: 8
            color: theme.surface_alt
            Text { anchors.centerIn: parent; text: "Cancel"; color: theme.text; font.family: theme.font_family; font.pixelSize: 14 }
            MouseArea { anchors.fill: parent; onClicked: backend.panelAction("cancel", {}) }
        }
        Rectangle {
            width: 90
            height: 38
            radius: 8
            color: theme.danger
            Text { anchors.centerIn: parent; text: "Delete"; color: "white"; font.family: theme.font_family; font.pixelSize: 14; font.bold: true }
            MouseArea { anchors.fill: parent; onClicked: backend.panelAction("confirm", {}) }
        }
    }

    focus: true
    Keys.onReturnPressed: backend.panelAction("confirm", {})
}
