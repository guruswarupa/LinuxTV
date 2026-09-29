import QtQuick 2.15

Column {
    id: root
    spacing: 12

    Text {
        width: parent.width
        text: "Set the phone credentials required to control LinuxTV remotely (password: 8+ characters). Leave all fields empty to pair phones with the code below instead."
        color: theme.text_muted
        font.family: theme.font_family
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }

    Text {
        width: parent.width
        text: "Pairing code: " + (backend.panelData.pairingCode || "")
        color: theme.text
        font.family: theme.font_family
        font.pixelSize: 16
        font.bold: true
    }

    Text { text: "Username"; color: theme.text_muted; font.family: theme.font_family; font.pixelSize: 12 }
    Rectangle {
        width: parent.width
        height: 40
        radius: 8
        color: theme.surface_alt
        border.width: userInput.activeFocus ? 2 : 0
        border.color: theme.accent

        TextInput {
            id: userInput
            anchors.fill: parent
            anchors.margins: 10
            verticalAlignment: TextInput.AlignVCenter
            color: theme.text
            font.family: theme.font_family
            font.pixelSize: 14
            text: backend.panelData.username || ""
            selectByMouse: true
            KeyNavigation.tab: passInput
        }
    }

    Text { text: "Password"; color: theme.text_muted; font.family: theme.font_family; font.pixelSize: 12 }
    Rectangle {
        width: parent.width
        height: 40
        radius: 8
        color: theme.surface_alt
        border.width: passInput.activeFocus ? 2 : 0
        border.color: theme.accent

        TextInput {
            id: passInput
            anchors.fill: parent
            anchors.margins: 10
            verticalAlignment: TextInput.AlignVCenter
            color: theme.text
            font.family: theme.font_family
            font.pixelSize: 14
            echoMode: TextInput.Password
            selectByMouse: true
            KeyNavigation.tab: confirmInput
            KeyNavigation.backtab: userInput
        }
    }

    Text { text: "Confirm password"; color: theme.text_muted; font.family: theme.font_family; font.pixelSize: 12 }
    Rectangle {
        width: parent.width
        height: 40
        radius: 8
        color: theme.surface_alt
        border.width: confirmInput.activeFocus ? 2 : 0
        border.color: theme.accent

        TextInput {
            id: confirmInput
            anchors.fill: parent
            anchors.margins: 10
            verticalAlignment: TextInput.AlignVCenter
            color: theme.text
            font.family: theme.font_family
            font.pixelSize: 14
            echoMode: TextInput.Password
            selectByMouse: true
            KeyNavigation.backtab: passInput
            Keys.onReturnPressed: root.save()
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
        backend.panelAction("save", { username: userInput.text, password: passInput.text, confirmPassword: confirmInput.text })
    }

    Component.onCompleted: userInput.forceActiveFocus()
}
