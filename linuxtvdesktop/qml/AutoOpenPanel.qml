import QtQuick 2.15

Column {
    id: root
    spacing: 12
    property string selectedKind: backend.panelData.selectedKind || ""
    property string selectedTarget: backend.panelData.selectedTarget || ""

    Text {
        width: parent.width
        text: "Choose which app or site opens automatically after LinuxTV sits idle."
        color: theme.text_muted
        font.family: theme.font_family
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }

    Rectangle {
        width: parent.width
        height: Math.min(200, Math.max(44, listView.contentHeight))
        radius: 8
        color: theme.surface_alt
        clip: true

        ListView {
            id: listView
            anchors.fill: parent
            model: [{ kind: "", target: "", label: "Disabled" }].concat(backend.panelData.options || [])
            delegate: Rectangle {
                width: listView.width
                height: 40
                color: (root.selectedKind === modelData.kind && root.selectedTarget === modelData.target) ? theme.accent : "transparent"
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    elide: Text.ElideRight
                    text: modelData.label
                    color: theme.text
                    font.family: theme.font_family
                    font.pixelSize: 13
                }
                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        root.selectedKind = modelData.kind
                        root.selectedTarget = modelData.target
                    }
                }
            }
        }
    }

    Text { text: "Idle delay (seconds)"; color: theme.text_muted; font.family: theme.font_family; font.pixelSize: 12 }
    Rectangle {
        width: 140
        height: 40
        radius: 8
        color: theme.surface_alt
        border.width: delayInput.activeFocus ? 2 : 0
        border.color: theme.accent

        TextInput {
            id: delayInput
            anchors.fill: parent
            anchors.margins: 10
            verticalAlignment: TextInput.AlignVCenter
            color: theme.text
            font.family: theme.font_family
            font.pixelSize: 14
            text: backend.panelData.delaySeconds || "10"
            validator: IntValidator { bottom: 1 }
            selectByMouse: true
        }
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
            color: theme.accent
            Text { anchors.centerIn: parent; text: "Save"; color: "white"; font.family: theme.font_family; font.pixelSize: 14; font.bold: true }
            MouseArea {
                anchors.fill: parent
                onClicked: backend.panelAction("save", { kind: root.selectedKind, target: root.selectedTarget, delaySeconds: delayInput.text })
            }
        }
    }
}
