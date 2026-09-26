import QtQuick 2.15

Column {
    id: root
    spacing: 12
    property string typeValue: backend.panelData.type || "Application"
    readonly property bool allowTypeChange: backend.panelData.allowTypeChange !== false

    Text {
        width: parent.width
        text: root.typeValue === "Website" ? "Create a launcher for a website." : "Create a launcher for an installed app command."
        color: theme.text_muted
        font.family: theme.font_family
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }

    Row {
        spacing: 8
        visible: root.allowTypeChange
        Repeater {
            model: ["Application", "Website"]
            delegate: Rectangle {
                width: 120
                height: 36
                radius: 8
                color: modelData === root.typeValue ? theme.accent : theme.surface_alt
                Text {
                    anchors.centerIn: parent
                    text: modelData
                    color: modelData === root.typeValue ? "white" : theme.text
                    font.family: theme.font_family
                    font.pixelSize: 13
                    font.bold: modelData === root.typeValue
                }
                MouseArea { anchors.fill: parent; onClicked: root.typeValue = modelData }
            }
        }
    }

    Text { text: "Name"; color: theme.text_muted; font.family: theme.font_family; font.pixelSize: 12 }
    Rectangle {
        width: parent.width
        height: 40
        radius: 8
        color: theme.surface_alt
        border.width: nameInput.activeFocus ? 2 : 0
        border.color: theme.accent

        TextInput {
            id: nameInput
            anchors.fill: parent
            anchors.margins: 10
            verticalAlignment: TextInput.AlignVCenter
            color: theme.text
            font.family: theme.font_family
            font.pixelSize: 14
            text: backend.panelData.name || ""
            selectByMouse: true
            KeyNavigation.tab: valueInput
            Keys.onReturnPressed: valueInput.forceActiveFocus()
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: 10
            text: "Spotify, YouTube, Kodi..."
            color: theme.text_muted
            font.family: theme.font_family
            font.pixelSize: 14
            visible: nameInput.text.length === 0
        }
    }

    Text {
        text: root.typeValue === "Website" ? "Website URL" : "Launch command"
        color: theme.text_muted
        font.family: theme.font_family
        font.pixelSize: 12
    }
    Rectangle {
        width: parent.width
        height: 40
        radius: 8
        color: theme.surface_alt
        border.width: valueInput.activeFocus ? 2 : 0
        border.color: theme.accent

        TextInput {
            id: valueInput
            anchors.fill: parent
            anchors.margins: 10
            verticalAlignment: TextInput.AlignVCenter
            color: theme.text
            font.family: theme.font_family
            font.pixelSize: 14
            text: backend.panelData.value || ""
            selectByMouse: true
            KeyNavigation.backtab: nameInput
            Keys.onReturnPressed: root.save()
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: 10
            text: root.typeValue === "Website" ? "https://www.youtube.com" : "flatpak run com.spotify.Client"
            color: theme.text_muted
            font.family: theme.font_family
            font.pixelSize: 14
            visible: valueInput.text.length === 0
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
            MouseArea { anchors.fill: parent; onClicked: root.save() }
        }
    }

    function save() {
        backend.panelAction("save", { type: root.typeValue, name: nameInput.text, value: valueInput.text })
    }

    Component.onCompleted: nameInput.forceActiveFocus()
}
